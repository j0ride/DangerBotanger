import sqlite3
import time
from collections import Counter
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
        columns = {row["name"] for row in self.db.execute("PRAGMA table_info(requests)")}
        for name, definition in [("sent_at", "REAL"), ("observed", "INTEGER NOT NULL DEFAULT 0")]:
            if name not in columns:
                self.db.execute(f"ALTER TABLE requests ADD COLUMN {name} {definition}")
        self.db.commit()
        self.db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self.db.execute("""CREATE TABLE IF NOT EXISTS playback_tracking (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1), uri TEXT,
            request_id INTEGER, progress_ms INTEGER, device_id TEXT)""")
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
            self.db.execute(
                "UPDATE requests SET status=?, sent_at=CASE WHEN ? IN ('sending','sent','uncertain') THEN ? ELSE sent_at END WHERE id=?",
                (status, status, time.time(), request_id))

    def user_slots(self, user, spotify_items, now=None, current_item=None):
        """Attribute Spotify URI occurrences to stored requests, plus in-flight reservations.

        Spotify exposes no requester IDs. Attribution relies on the queue snapshot;
        manual additions of the same URI cannot be distinguished from bot additions.
        """
        now = time.time() if now is None else now
        remaining = Counter(item.get("uri") for item in spotify_items
                            if isinstance(item, dict) and item.get("uri"))
        rows = self.db.execute(
            "SELECT * FROM requests WHERE status IN ('sent','uncertain') ORDER BY id").fetchall()
        if current_item is not None:
            self.track_playback({"item": current_item})
        with self.db:
            for row in rows:
                if self.get(row["id"])["status"] == "playing":
                    continue
                if remaining[row["uri"]] > 0:
                    remaining[row["uri"]] -= 1
                    self.db.execute("UPDATE requests SET observed=1, status='sent' WHERE id=?", (row["id"],))
                elif row["sent_at"] is None:
                    # Legacy records have no evidence of a recent delivery.
                    # Autoplay can produce long snapshots; that must not keep
                    # historical requests reserved forever.
                    self.db.execute("UPDATE requests SET status='completed' WHERE id=?", (row["id"],))
                elif row["status"] == "sent":
                    # Allow Spotify time to expose a just-delivered request.
                    recent = row["sent_at"] is not None and now - row["sent_at"] < 60
                    # A long snapshot may omit its tail. Keep unknown requests
                    # reserved rather than treating omitted items as played.
                    if len(spotify_items) < 20 and (row["observed"] or not recent):
                        self.db.execute("UPDATE requests SET status='completed' WHERE id=?", (row["id"],))
        return self.db.execute(
            "SELECT COUNT(*) FROM requests WHERE user=? COLLATE NOCASE "
            "AND status IN ('pending','sending','sent','uncertain')", (user,)).fetchone()[0]

    def track_playback(self, playback, now=None):
        """Bind one delivered request to an observed playback, not every !np call."""
        now = time.time() if now is None else now
        item = playback.get("item") if playback else None
        uri = item.get("uri") if isinstance(item, dict) else None
        progress = playback.get("progress_ms") if playback else None
        device = (playback.get("device") or {}).get("id") if playback else None
        previous = self.db.execute("SELECT * FROM playback_tracking WHERE singleton=1").fetchone()
        repeated = (previous and progress is not None and previous["progress_ms"] is not None
                    and previous["progress_ms"] - progress > 5000)
        changed_device = previous and device and previous["device_id"] and device != previous["device_id"]
        same_playback = previous and previous["uri"] == uri and not repeated and not changed_device
        with self.db:
            if same_playback:
                self.db.execute(
                    "UPDATE playback_tracking SET progress_ms=COALESCE(?,progress_ms), "
                    "device_id=COALESCE(?,device_id) WHERE singleton=1", (progress, device))
                row = self.get(previous["request_id"]) if previous["request_id"] else None
                return row["user"] if row else None
            if previous and previous["request_id"]:
                self.db.execute("UPDATE requests SET status='completed' WHERE id=?",
                                (previous["request_id"],))
            request = None
            if uri:
                # A request sent after this playback began belongs to a later
                # occurrence of this same song, not the currently playing one.
                started_at = now - progress / 1000 if progress is not None else now
                request = self.db.execute(
                    "SELECT * FROM requests WHERE uri=? AND status IN ('sent','uncertain') "
                    "AND ((sent_at IS NOT NULL AND sent_at<=?) OR (sent_at IS NULL AND observed=1)) ORDER BY id LIMIT 1",
                    (uri, started_at + 5)).fetchone()
            request_id = request["id"] if request else None
            if request_id:
                self.db.execute("UPDATE requests SET status='playing' WHERE id=?", (request_id,))
            self.db.execute(
                "INSERT INTO playback_tracking VALUES(1,?,?,?,?) "
                "ON CONFLICT(singleton) DO UPDATE SET uri=excluded.uri, request_id=excluded.request_id, "
                "progress_ms=excluded.progress_ms, device_id=excluded.device_id",
                (uri, request_id, progress, device))
            return request["user"] if request else None

    def close(self):
        self.db.close()
