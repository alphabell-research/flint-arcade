"""Durable queue and successful public game records."""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import re
import secrets
import sqlite3
import time


class QueueError(Exception):
    def __init__(self, message, status=429):
        self.message, self.status = message, status
        super().__init__(message)


class Storage:
    def __init__(self, data_dir: Path):
        data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.data_dir = data_dir.resolve()
        self.preview_dir = self.data_dir / "previews"
        self.path = data_dir / "arcade.sqlite3"
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS games (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL,
                    created_at TEXT NOT NULL, parent_id TEXT, model_label TEXT NOT NULL,
                    html TEXT NOT NULL, qa TEXT NOT NULL, provenance TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, prompt TEXT NOT NULL,
                    parent_id TEXT, status TEXT NOT NULL, stage TEXT NOT NULL,
                    message TEXT, error TEXT, game_id TEXT,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0, dataset_opt_in INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS queue_order ON jobs(status,created_at);
                CREATE INDEX IF NOT EXISTS daily_job_count ON jobs(created_at);
                CREATE TABLE IF NOT EXISTS rate_events (identity TEXT NOT NULL, created_at REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS rate_lookup ON rate_events(identity,created_at);
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
            if "dataset_opt_in" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN dataset_opt_in INTEGER NOT NULL DEFAULT 0")
        os.chmod(self.path, 0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def enqueue(self, owner, prompt, parent_id, identity, per_hour, queue_cap, dataset_opt_in=False, daily_cap=100):
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if parent_id and not db.execute("SELECT 1 FROM games WHERE id=?", (parent_id,)).fetchone():
                raise QueueError("The game to remix was not found.", 404)
            db.execute("DELETE FROM rate_events WHERE created_at<?", (now - 3600,))
            count = db.execute("SELECT count(*) FROM rate_events WHERE identity=?", (identity,)).fetchone()[0]
            if count >= per_hour:
                raise QueueError("Your hourly build limit has been reached. Try again later.")
            daily = db.execute("SELECT count(*) FROM jobs WHERE created_at>=?", (now - 86400,)).fetchone()[0]
            if daily >= daily_cap:
                raise QueueError("The arcade has reached its daily build limit. Please try again later.")
            pending = db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','generating','testing','repairing')").fetchone()[0]
            if pending >= queue_cap:
                raise QueueError("The arcade is busy. Please try again shortly.", 503)
            job_id = secrets.token_urlsafe(12)
            db.execute("INSERT INTO jobs(id,owner,prompt,parent_id,status,stage,created_at,updated_at,dataset_opt_in) VALUES (?,?,?,?,?,?,?,?,?)", (job_id, owner, prompt, parent_id, "queued", "queued", now, now, int(dataset_opt_in)))
            db.execute("INSERT INTO rate_events VALUES (?,?)", (identity, now))
            return job_id

    def job(self, job_id, owner=None):
        with self.connect() as db:
            query = "SELECT * FROM jobs WHERE id=?"
            args = (job_id,)
            if owner is not None:
                query += " AND owner=?"
                args += (owner,)
            row = db.execute(query, args).fetchone()
            return dict(row) if row else None

    def claim(self, max_attempts):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM jobs WHERE status IN ('generating','testing','repairing') LIMIT 1").fetchone():
                return None
            row = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at,id LIMIT 1").fetchone()
            if not row:
                return None
            if row["attempts"] >= max_attempts:
                db.execute("UPDATE jobs SET status='failed',stage='failed',error=?,updated_at=? WHERE id=?", ("This build could not be resumed. Please start a new game.", time.time(), row["id"]))
                return None
            db.execute("UPDATE jobs SET status='generating',stage='generating',attempts=attempts+1,updated_at=? WHERE id=?", (time.time(), row["id"]))
            result = dict(row)
            result["attempts"] += 1
            return result

    def recover(self, max_attempts):
        with self.connect() as db:
            db.execute("UPDATE jobs SET status='failed',stage='failed',error=?,updated_at=? WHERE status IN ('generating','testing','repairing') AND attempts>=?", ("This build was interrupted. Please start a new game.", time.time(), max_attempts))
            db.execute("UPDATE jobs SET status='queued',stage='queued',message=NULL,updated_at=? WHERE status IN ('generating','testing','repairing')", (time.time(),))

    def stage(self, job_id, status):
        if status not in {"generating", "testing", "repairing"}:
            raise ValueError("Invalid build stage")
        with self.connect() as db:
            db.execute("UPDATE jobs SET status=?,stage=?,message=NULL,updated_at=? WHERE id=? AND status IN ('generating','testing','repairing')", (status, status, time.time(), job_id))

    def fail(self, job_id, error="The game could not pass its play checks. Please try a different description."):
        with self.connect() as db:
            db.execute("UPDATE jobs SET status='failed',stage='failed',error=?,updated_at=? WHERE id=? AND status!='complete'", (error, time.time(), job_id))

    def publish(self, job, result, model_label):
        game_id = secrets.token_urlsafe(10)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM jobs WHERE id=?", (job["id"],)).fetchone()
            if not row or row[0] not in {"generating", "testing", "repairing"}:
                raise ValueError("Job is not active")
            db.execute("INSERT INTO games VALUES (?,?,?,?,?,?,?,?,?)", (game_id, str(result["title"])[:100], str(result["description"])[:400], datetime.now(timezone.utc).isoformat(), job["parent_id"], model_label, result["html"], json.dumps(result["qa"]), json.dumps(result.get("provenance", {}))))
            db.execute("UPDATE jobs SET status='complete',stage='complete',game_id=?,error=NULL,message=NULL,updated_at=? WHERE id=?", (game_id, time.time(), job["id"]))
        return game_id

    def game(self, game_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM games WHERE id=?", (game_id,)).fetchone()
            return dict(row) if row else None

    def preview_path(self, game_id):
        """Return only a fixed public PNG path, never a caller-supplied path."""
        if not isinstance(game_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", game_id):
            return None
        path = self.preview_dir / (game_id + ".png")
        if self.preview_dir.is_symlink() or path.is_symlink() or not path.is_file():
            return None
        return path

    def publish_preview(self, game_id, result):
        """Copy a measured screenshot from an accepted local generation run."""
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", game_id) or result.get("qa", {}).get("accepted") is not True:
            return False
        provenance = result.get("provenance", {})
        run_id, attempts = provenance.get("run_id"), provenance.get("attempts")
        if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", run_id) or not isinstance(attempts, list) or not 1 <= len(attempts) <= 5:
            return False
        source = self.data_dir / "builds" / run_id / str(len(attempts)) / "qa" / "desktop.png"
        if any(part.is_symlink() for part in (source, *source.parents[:4])) or not source.is_file() or source.stat().st_size > 5_000_000:
            return False
        content = source.read_bytes()
        if not content.startswith(b"\x89PNG\r\n\x1a\n") or self.preview_dir.is_symlink():
            return False
        self.preview_dir.mkdir(mode=0o700, exist_ok=True)
        destination = self.preview_dir / (game_id + ".png")
        temporary = self.preview_dir / (".preview-" + secrets.token_urlsafe(12))
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return True

    def games(self, limit=60):
        with self.connect() as db:
            rows = db.execute("SELECT id,title,description,created_at,parent_id,model_label FROM games ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
            return [dict(row) for row in rows]

    def import_game(self, game_id, result, model_label="Flint 8-bit"):
        """Import a lab-authored, previously validated seed without creating a user job."""
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", game_id):
            raise ValueError("Invalid seed identifier")
        if result.get("qa", {}).get("accepted") is not True or not result.get("html", "").strip():
            raise ValueError("Only accepted seeds can be imported")
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO games VALUES (?,?,?,?,?,?,?,?,?)", (game_id, str(result["title"])[:100], str(result["description"])[:400], datetime.now(timezone.utc).isoformat(), result.get("parent_id"), model_label, result["html"], json.dumps(result["qa"]), json.dumps(result.get("provenance", {}))))
        return game_id
