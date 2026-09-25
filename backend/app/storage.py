"""Versioned SQLite storage. Each operation owns a short-lived connection and transaction."""

import hashlib
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from app.errors import AppError

SCHEMA = """
CREATE TABLE reviews (
  review_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, client_request_id TEXT NOT NULL,
  language TEXT NOT NULL, source_code TEXT NOT NULL, review_result TEXT,
  status TEXT NOT NULL CHECK(status IN ('queued','running','completed','failed')),
  error_code TEXT, error_message TEXT, model_id TEXT, model_revision TEXT,
  retry_count INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, updated_at REAL NOT NULL,
  UNIQUE(user_id, client_request_id)
);
CREATE INDEX reviews_user_created ON reviews(user_id, created_at DESC, review_id DESC);
CREATE INDEX reviews_status_created ON reviews(status, created_at);
CREATE TABLE sessions (
  token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, login_id TEXT NOT NULL,
  csrf_token TEXT NOT NULL, expires_at REAL NOT NULL
);
CREATE INDEX sessions_expiry ON sessions(expires_at);
PRAGMA user_version = 1;
"""


class Store:
    def __init__(self, directory: Path):
        self.path = directory / "reviews.sqlite3"

    @contextmanager
    def connection(self, write=False):
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("PRAGMA synchronous=FULL")
            if write:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            if write:
                connection.commit()
        except BaseException:
            if write:
                connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                db.executescript("BEGIN IMMEDIATE;\n" + SCHEMA + "\nCOMMIT;")
            elif version != 1:
                raise RuntimeError("Unsupported database schema version.")
        self.path.chmod(0o600)

    def ping(self):
        with self.connection(write=True) as db:
            db.execute("DELETE FROM sessions WHERE expires_at <= ?", (time.time(),))

    def session_create(self, token, user, csrf_token, expires_at):
        with self.connection(write=True) as db:
            db.execute("DELETE FROM sessions WHERE expires_at <= ?", (time.time(),))
            db.execute(
                "INSERT INTO sessions VALUES (?, ?, ?, ?, ?)",
                (self.digest(token), user["user_id"], user["login_id"], csrf_token, expires_at),
            )

    @staticmethod
    def digest(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def session_get(self, token):
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM sessions WHERE token_hash = ? AND expires_at > ?",
                (self.digest(token), time.time()),
            ).fetchone()
            return dict(row) if row else None

    def session_delete(self, token):
        with self.connection(write=True) as db:
            db.execute("DELETE FROM sessions WHERE token_hash = ?", (self.digest(token),))

    def create_review(self, user_id, request_id, language, source, capacity):
        with self.connection(write=True) as db:
            old = db.execute(
                "SELECT * FROM reviews WHERE user_id = ? AND client_request_id = ?",
                (user_id, request_id),
            ).fetchone()
            if old:
                if old["source_code"] != source or old["language"] != language:
                    raise AppError(
                        "idempotency_conflict", "This request key has different input.", 409
                    )
                return dict(old) | {"_created": False}
            active = db.execute(
                "SELECT count(*) FROM reviews WHERE status IN ('queued','running')"
            ).fetchone()[0]
            if active >= capacity + 1:
                raise AppError("queue_full", "The review queue is full. Try again later.", 429)
            queued = db.execute("SELECT count(*) FROM reviews WHERE status = 'queued'").fetchone()[
                0
            ]
            if queued >= capacity:
                raise AppError("queue_full", "The review queue is full. Try again later.", 429)
            if db.execute(
                "SELECT 1 FROM reviews WHERE user_id = ? AND status IN ('queued','running')",
                (user_id,),
            ).fetchone():
                raise AppError("review_active", "A review is already active for your account.", 409)
            review_id, now = str(uuid4()), time.time()
            db.execute(
                "INSERT INTO reviews(review_id,user_id,client_request_id,language,source_code,"
                "status,created_at,updated_at) VALUES (?,?,?,?,?,'queued',?,?)",
                (review_id, user_id, request_id, language, source, now, now),
            )
            return dict(
                db.execute("SELECT * FROM reviews WHERE review_id=?", (review_id,)).fetchone()
            ) | {"_created": True, "_queue_depth": queued + 1}

    def queue_depth(self):
        """Return only the queued count; never read or copy review content."""

        with self.connection() as db:
            return db.execute("SELECT count(*) FROM reviews WHERE status='queued'").fetchone()[0]

    def get_review(self, user_id, review_id):
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM reviews WHERE user_id=? AND review_id=?", (user_id, review_id)
            ).fetchone()
            if not row:
                raise AppError("review_not_found", "Review not found.", 404)
            return dict(row)

    def history(self, user_id, limit, before=None):
        with self.connection() as db:
            args = [user_id]
            cursor_filter = ""
            if before:
                cursor = db.execute(
                    "SELECT created_at FROM reviews WHERE user_id=? AND review_id=?",
                    (user_id, before),
                ).fetchone()
                if not cursor:
                    raise AppError("invalid_cursor", "Invalid history cursor.", 400)
                cursor_filter = " AND (created_at,review_id) < (?,?)"
                args.extend([cursor[0], before])
            args.append(limit + 1)
            rows = db.execute(
                "SELECT review_id,language,status,error_code,created_at,updated_at FROM reviews "
                "WHERE user_id=?"
                + cursor_filter
                + " ORDER BY created_at DESC,review_id DESC LIMIT ?",
                args,
            ).fetchall()
            items = [dict(row) for row in rows[:limit]]
            return {
                "items": items,
                "next_cursor": items[-1]["review_id"] if len(rows) > limit else None,
            }

    def recover(self, max_retries, capacity):
        with self.connection(write=True) as db:
            now = time.time()
            db.execute(
                "UPDATE reviews SET status='failed', error_code='interrupted', "
                "error_message='Review interrupted; retry limit reached.', updated_at=? "
                "WHERE status='running' AND retry_count>=?",
                (now, max_retries),
            )
            db.execute(
                "UPDATE reviews SET status='queued',retry_count=retry_count+1,updated_at=? "
                "WHERE status='running'",
                (now,),
            )
            db.execute(
                "UPDATE reviews SET status='failed',error_code='queue_recovery_limit', "
                "error_message='Queue capacity was reduced. Submit a new review.',updated_at=? "
                "WHERE review_id IN (SELECT review_id FROM reviews WHERE status='queued' "
                "ORDER BY created_at LIMIT -1 OFFSET ?)",
                (now, capacity),
            )

    def claim(self):
        with self.connection(write=True) as db:
            row = db.execute(
                "SELECT * FROM reviews WHERE status='queued' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if not row:
                return None
            db.execute(
                "UPDATE reviews SET status='running',updated_at=? WHERE review_id=?",
                (time.time(), row["review_id"]),
            )
            return dict(row)

    def finish(self, review_id, *, result=None, error=None, model_id=None, revision=None):
        with self.connection(write=True) as db:
            db.execute(
                "UPDATE reviews SET status=?,review_result=?,error_code=?,error_message=?,"
                "model_id=?,model_revision=?,updated_at=? WHERE review_id=? AND status='running'",
                (
                    "failed" if error else "completed",
                    result,
                    error.code if error else None,
                    error.message if error else None,
                    model_id,
                    revision,
                    time.time(),
                    review_id,
                ),
            )
