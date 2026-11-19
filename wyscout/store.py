from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import json
import sqlite3
from pathlib import Path
from uuid import uuid4

from .settings import Settings, data_directory


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


SCHEMA = '''
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY, created TEXT NOT NULL, updated TEXT NOT NULL,
    mode TEXT NOT NULL, settings TEXT NOT NULL, status TEXT NOT NULL,
    stop INTEGER NOT NULL DEFAULT 0, output TEXT, message TEXT NOT NULL DEFAULT '', base_job TEXT
);
CREATE TABLE IF NOT EXISTS batches (
    job_id TEXT NOT NULL REFERENCES jobs(id), n INTEGER NOT NULL, league TEXT NOT NULL,
    timeframe TEXT NOT NULL, position TEXT NOT NULL, layout TEXT NOT NULL, season TEXT,
    expected INTEGER, downloaded INTEGER, status TEXT NOT NULL, message TEXT NOT NULL DEFAULT '',
    paths TEXT NOT NULL DEFAULT '[]', updated TEXT NOT NULL, PRIMARY KEY (job_id, n)
);
CREATE TABLE IF NOT EXISTS preferences (key TEXT PRIMARY KEY, value TEXT NOT NULL);
'''


class Store:
    def __init__(self, root: Path | None = None):
        self.root = root or data_directory()
        self.root.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.root / 'jobs.sqlite3', timeout=12)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA foreign_keys = ON')
            with db:
                yield db
        finally:
            db.close()

    def create(self, settings, mode, base_job=None):
        batches = list(settings.batches())
        job_id, timestamp = uuid4().hex, now()
        with self.connect() as db:
            db.execute('INSERT INTO jobs (id,created,updated,mode,settings,status,base_job) VALUES (?,?,?,?,?,?,?)',
                       (job_id, timestamp, timestamp, mode, json.dumps(asdict(settings)), 'oczekuje', base_job))
            db.executemany('''INSERT INTO batches (job_id,n,league,timeframe,position,layout,status,updated)
                            VALUES (?,?,?,?,?,?,?,?)''',
                           [(job_id, i, b.league, b.timeframe, b.position, b.layout, 'oczekuje', timestamp)
                            for i, b in enumerate(batches)])
        return job_id

    def get(self, job_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            return dict(row) if row else None

    def settings(self, job_id):
        raw = json.loads(self.get(job_id)['settings'])
        return Settings(**{k: tuple(v) if isinstance(v, list) else v for k, v in raw.items()})

    def history(self):
        with self.connect() as db:
            rows = db.execute('''SELECT j.*, (SELECT COALESCE(SUM(b.downloaded),0) FROM batches b
                              WHERE b.job_id=j.id AND b.status IN ('kompletny','pusty')) AS downloaded
                              FROM jobs j ORDER BY created DESC, rowid DESC''').fetchall()
            return [dict(row) for row in rows]

    def batches(self, job_id):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM batches WHERE job_id=? ORDER BY n', (job_id,))]

    def job_update(self, job, status, message='', *, output=None):
        with self.connect() as db:
            db.execute('UPDATE jobs SET status=?, message=?, updated=?, output=? WHERE id=?', (status, message, now(), output, job))

    def stop(self, job_id, value):
        with self.connect() as db:
            db.execute('UPDATE jobs SET stop=? WHERE id=?', (int(value), job_id))

    def batch_update(self, job, index, status, *, season=None, expected=None, downloaded=None, paths=(), message=''):
        with self.connect() as db:
            db.execute('''UPDATE batches SET status=?,season=?,expected=?,downloaded=?,paths=?,message=?,updated=?
                          WHERE job_id=? AND n=?''', (status, season, expected, downloaded, json.dumps(paths), message, now(), job, index))

    def preference(self, key, default=None):
        with self.connect() as db:
            row = db.execute('SELECT value FROM preferences WHERE key=?', (key,)).fetchone()
            return json.loads(row['value']) if row else default

    def remember(self, key, data):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO preferences VALUES (?,?)', (key, json.dumps(data)))

    def publish(self, job, output, message):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO preferences VALUES (?,?)', ('published', json.dumps(job)))
            db.execute('UPDATE jobs SET status=?,message=?,updated=?,output=? WHERE id=?',
                       ('ukończono', message, now(), output, job))

    def path(self, relative):
        base = self.root.resolve()
        path = (base / relative).resolve()
        if not path.is_relative_to(base):
            raise ValueError('Invalid path.')
        return path
