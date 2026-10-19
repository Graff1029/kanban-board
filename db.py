"""Здесь только работа с SQLite."""
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def db_path():
    return Path(os.getenv('DB_PATH', str(ROOT / 'data' / 'kanban.db')))


@contextmanager
def connection(write=False):
    db = sqlite3.connect(db_path(), timeout=15)
    db.row_factory = sqlite3.Row
    db.create_function('casefold', 1, lambda value: value.casefold() if value else '', deterministic=True)
    db.execute('PRAGMA foreign_keys = ON')
    try:
        # Запись идёт одной операцией. Две задачи не обойдут WIP-лимит.
        if write:
            db.execute('BEGIN IMMEDIATE')
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db():
    db_path().parent.mkdir(parents=True, exist_ok=True)
    with connection() as db:
        db.execute('PRAGMA journal_mode = WAL')
        if db.execute('PRAGMA user_version').fetchone()[0] == 0:
            db.executescript(SCHEMA)


def rows(db, sql, args=()):
    return [dict(row) for row in db.execute(sql, args).fetchall()]


# Первая версия базы. При повторном запуске она не создаётся заново.
SCHEMA = """
-- База создаётся один раз. При следующем запуске данные остаются.
CREATE TABLE users (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL,
 email TEXT NOT NULL UNIQUE, password TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('admin','manager','developer'))
);
CREATE TABLE sessions (
 jti TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 expires INTEGER NOT NULL
);
CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '');
CREATE TABLE boards (
 id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 name TEXT NOT NULL
);
CREATE TABLE columns (
 id INTEGER PRIMARY KEY, board_id INTEGER NOT NULL REFERENCES boards(id) ON DELETE CASCADE,
 name TEXT NOT NULL, wip INTEGER NOT NULL DEFAULT 0 CHECK(wip >= 0), position INTEGER NOT NULL
);
CREATE TABLE tasks (
 id INTEGER PRIMARY KEY, column_id INTEGER NOT NULL REFERENCES columns(id) ON DELETE CASCADE,
 title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
 assignee_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
 priority TEXT NOT NULL CHECK(priority IN ('low','medium','high')),
 due_date TEXT, labels TEXT NOT NULL DEFAULT ''
);
CREATE TABLE comments (
 id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
 user_id INTEGER NOT NULL REFERENCES users(id), text TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE history (
 id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
 user_id INTEGER NOT NULL REFERENCES users(id), text TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE attachments (
 id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
 user_id INTEGER NOT NULL REFERENCES users(id), name TEXT NOT NULL, data BLOB NOT NULL,
 content_type TEXT NOT NULL
);
CREATE INDEX task_column ON tasks(column_id);
CREATE INDEX column_board ON columns(board_id);
CREATE INDEX board_project ON boards(project_id);
CREATE INDEX comment_task ON comments(task_id);
CREATE INDEX history_task ON history(task_id);
CREATE INDEX attachment_task ON attachments(task_id);
PRAGMA user_version = 1;
"""
