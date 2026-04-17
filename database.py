"""Storage abstraction: Postgres in production, SQLite locally.

Detection rule: if `DATABASE_URL` starts with `postgres://` or `postgresql://`,
use psycopg; otherwise fall back to SQLite at `./community_capture.db`
(or `/tmp/community_capture.db` on Vercel, which is ephemeral).

Schema is identical between engines. A tiny `_exec()` helper rewrites
`?` placeholders to `%s` for Postgres so call sites stay the same.
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
IS_POSTGRES = DATABASE_URL.startswith(("postgres://", "postgresql://"))

if not IS_POSTGRES:
    if os.environ.get("VERCEL"):
        SQLITE_PATH = Path("/tmp/community_capture.db")
    else:
        SQLITE_PATH = Path(__file__).parent / "community_capture.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------- connection + query abstraction ----------

def _get_conn():
    if IS_POSTGRES:
        import psycopg
        return psycopg.connect(DATABASE_URL, autocommit=False)
    conn = sqlite3.connect(str(SQLITE_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if not os.environ.get("VERCEL"):
        conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ph(sql: str) -> str:
    """Rewrite ? -> %s for Postgres; no-op for SQLite."""
    return sql.replace("?", "%s") if IS_POSTGRES else sql


def _exec(conn, sql: str, params: tuple = ()):
    cur = conn.cursor()
    cur.execute(_ph(sql), params)
    return cur


def _row_to_dict(cur, row) -> Dict[str, Any]:
    if row is None:
        return None
    if IS_POSTGRES:
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))
    return dict(row)


def _rows_to_dicts(cur, rows) -> List[Dict[str, Any]]:
    if IS_POSTGRES:
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in rows]
    return [dict(r) for r in rows]


# ---------- schema ----------

_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    external_id TEXT NOT NULL UNIQUE,
    subreddit TEXT,
    author TEXT,
    title TEXT,
    text TEXT,
    permalink TEXT,
    created_utc TEXT,
    urgency_score REAL DEFAULT 0,
    topic TEXT,
    engagement_level TEXT,
    ingested_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    draft_text TEXT NOT NULL,
    model TEXT,
    prompt_version TEXT,
    safety_passed INTEGER NOT NULL DEFAULT 1,
    safety_violations TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    reviewer_note TEXT,
    generated_at TEXT NOT NULL,
    decided_at TEXT
);

CREATE TABLE IF NOT EXISTS tracking_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER REFERENCES drafts(id) ON DELETE SET NULL,
    tracking_id TEXT NOT NULL UNIQUE,
    dest_url TEXT NOT NULL,
    utm_source TEXT,
    utm_medium TEXT,
    utm_campaign TEXT,
    utm_content TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS clicks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tracking_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    ip_hash TEXT,
    user_agent TEXT
);

CREATE TABLE IF NOT EXISTS conversions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tracking_id TEXT,
    type TEXT NOT NULL,
    email_hash TEXT,
    ts TEXT NOT NULL
);
"""

_POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id SERIAL PRIMARY KEY,
    platform TEXT NOT NULL,
    external_id TEXT NOT NULL UNIQUE,
    subreddit TEXT,
    author TEXT,
    title TEXT,
    text TEXT,
    permalink TEXT,
    created_utc TEXT,
    urgency_score REAL DEFAULT 0,
    topic TEXT,
    engagement_level TEXT,
    ingested_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS drafts (
    id SERIAL PRIMARY KEY,
    post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    draft_text TEXT NOT NULL,
    model TEXT,
    prompt_version TEXT,
    safety_passed INTEGER NOT NULL DEFAULT 1,
    safety_violations TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    reviewer_note TEXT,
    generated_at TEXT NOT NULL,
    decided_at TEXT
);

CREATE TABLE IF NOT EXISTS tracking_links (
    id SERIAL PRIMARY KEY,
    draft_id INTEGER REFERENCES drafts(id) ON DELETE SET NULL,
    tracking_id TEXT NOT NULL UNIQUE,
    dest_url TEXT NOT NULL,
    utm_source TEXT,
    utm_medium TEXT,
    utm_campaign TEXT,
    utm_content TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS clicks (
    id SERIAL PRIMARY KEY,
    tracking_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    ip_hash TEXT,
    user_agent TEXT
);

CREATE TABLE IF NOT EXISTS conversions (
    id SERIAL PRIMARY KEY,
    tracking_id TEXT,
    type TEXT NOT NULL,
    email_hash TEXT,
    ts TEXT NOT NULL
);
"""


def init_db() -> None:
    conn = _get_conn()
    try:
        if IS_POSTGRES:
            # psycopg can execute multiple statements via execute() when passed as one string
            cur = conn.cursor()
            for stmt in _POSTGRES_SCHEMA.strip().split(";\n\n"):
                stmt = stmt.strip()
                if stmt:
                    cur.execute(stmt)
        else:
            conn.executescript(_SQLITE_SCHEMA)
        conn.commit()
    finally:
        conn.close()


# ---------- posts ----------

def upsert_post(p: Dict[str, Any]) -> Optional[int]:
    """Insert post if external_id is new. Returns row id (or None if duplicate)."""
    conn = _get_conn()
    try:
        if IS_POSTGRES:
            cur = conn.cursor()
            cur.execute(
                _ph(
                    """
                    INSERT INTO posts
                        (platform, external_id, subreddit, author, title, text, permalink,
                         created_utc, urgency_score, topic, engagement_level, ingested_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (external_id) DO NOTHING
                    RETURNING id
                    """
                ),
                (
                    p["platform"], p["external_id"], p.get("subreddit"), p.get("author"),
                    p.get("title"), p.get("text"), p.get("permalink"), p.get("created_utc"),
                    p.get("urgency_score", 0), p.get("topic"), p.get("engagement_level"), _now(),
                ),
            )
            row = cur.fetchone()
            conn.commit()
            return row[0] if row else None
        else:
            try:
                cur = conn.execute(
                    """
                    INSERT INTO posts
                        (platform, external_id, subreddit, author, title, text, permalink,
                         created_utc, urgency_score, topic, engagement_level, ingested_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        p["platform"], p["external_id"], p.get("subreddit"), p.get("author"),
                        p.get("title"), p.get("text"), p.get("permalink"), p.get("created_utc"),
                        p.get("urgency_score", 0), p.get("topic"), p.get("engagement_level"), _now(),
                    ),
                )
                conn.commit()
                return cur.lastrowid
            except sqlite3.IntegrityError:
                return None
    finally:
        conn.close()


def list_posts_without_drafts(limit: int = 50) -> List[Dict[str, Any]]:
    conn = _get_conn()
    try:
        cur = _exec(
            conn,
            """
            SELECT p.* FROM posts p
            LEFT JOIN drafts d ON d.post_id = p.id
            WHERE d.id IS NULL
            ORDER BY p.urgency_score DESC, p.ingested_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = cur.fetchall()
        return _rows_to_dicts(cur, rows)
    finally:
        conn.close()


def get_post(post_id: int) -> Optional[Dict[str, Any]]:
    conn = _get_conn()
    try:
        cur = _exec(conn, "SELECT * FROM posts WHERE id = ?", (post_id,))
        row = cur.fetchone()
        return _row_to_dict(cur, row)
    finally:
        conn.close()


# ---------- drafts ----------

def insert_draft(
    post_id: int, draft_text: str, model: str, prompt_version: str,
    safety_passed: bool, safety_violations: List[str],
) -> int:
    conn = _get_conn()
    try:
        if IS_POSTGRES:
            cur = conn.cursor()
            cur.execute(
                _ph(
                    """
                    INSERT INTO drafts
                        (post_id, draft_text, model, prompt_version,
                         safety_passed, safety_violations, status, generated_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
                    RETURNING id
                    """
                ),
                (post_id, draft_text, model, prompt_version,
                 1 if safety_passed else 0, json.dumps(safety_violations), _now()),
            )
            new_id = cur.fetchone()[0]
            conn.commit()
            return new_id
        else:
            cur = conn.execute(
                """
                INSERT INTO drafts
                    (post_id, draft_text, model, prompt_version,
                     safety_passed, safety_violations, status, generated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (post_id, draft_text, model, prompt_version,
                 1 if safety_passed else 0, json.dumps(safety_violations), _now()),
            )
            conn.commit()
            return cur.lastrowid
    finally:
        conn.close()


def list_queue(status: str = "pending", limit: int = 100) -> List[Dict[str, Any]]:
    conn = _get_conn()
    try:
        cur = _exec(
            conn,
            """
            SELECT
                d.id           AS draft_id,
                d.draft_text, d.model, d.prompt_version,
                d.safety_passed, d.safety_violations, d.status,
                d.reviewer_note, d.generated_at, d.decided_at,
                p.id           AS post_id,
                p.platform, p.subreddit, p.author, p.title, p.text, p.permalink,
                p.created_utc, p.urgency_score, p.topic, p.engagement_level
            FROM drafts d
            JOIN posts p ON p.id = d.post_id
            WHERE d.status = ?
            ORDER BY p.urgency_score DESC, d.generated_at DESC
            LIMIT ?
            """,
            (status, limit),
        )
        rows = cur.fetchall()
        items = _rows_to_dicts(cur, rows)
        for d in items:
            d["safety_violations"] = json.loads(d["safety_violations"] or "[]")
            d["safety_passed"] = bool(d["safety_passed"])
        return items
    finally:
        conn.close()


def get_draft(draft_id: int) -> Optional[Dict[str, Any]]:
    conn = _get_conn()
    try:
        cur = _exec(
            conn,
            """
            SELECT d.*, p.platform, p.subreddit, p.author, p.title, p.text, p.permalink
            FROM drafts d JOIN posts p ON p.id = d.post_id
            WHERE d.id = ?
            """,
            (draft_id,),
        )
        row = cur.fetchone()
        d = _row_to_dict(cur, row)
        if not d:
            return None
        d["safety_violations"] = json.loads(d["safety_violations"] or "[]")
        d["safety_passed"] = bool(d["safety_passed"])
        return d
    finally:
        conn.close()


def update_draft_status(
    draft_id: int, status: str, reviewer_note: Optional[str] = None
) -> None:
    conn = _get_conn()
    try:
        _exec(
            conn,
            "UPDATE drafts SET status = ?, reviewer_note = ?, decided_at = ? WHERE id = ?",
            (status, reviewer_note, _now(), draft_id),
        )
        conn.commit()
    finally:
        conn.close()


def replace_draft_text(
    draft_id: int, draft_text: str, model: str, prompt_version: str,
    safety_passed: bool, safety_violations: List[str],
) -> None:
    conn = _get_conn()
    try:
        _exec(
            conn,
            """
            UPDATE drafts SET
                draft_text = ?, model = ?, prompt_version = ?,
                safety_passed = ?, safety_violations = ?,
                status = 'pending', decided_at = NULL,
                generated_at = ?
            WHERE id = ?
            """,
            (draft_text, model, prompt_version,
             1 if safety_passed else 0, json.dumps(safety_violations),
             _now(), draft_id),
        )
        conn.commit()
    finally:
        conn.close()


# ---------- tracking ----------

def create_tracking_link(
    draft_id: int, dest_url: str,
    utm_source: str = "reddit", utm_medium: str = "community",
    utm_campaign: str = "community-capture", utm_content: Optional[str] = None,
) -> str:
    tracking_id = secrets.token_urlsafe(8)
    conn = _get_conn()
    try:
        _exec(
            conn,
            """
            INSERT INTO tracking_links
                (draft_id, tracking_id, dest_url, utm_source, utm_medium,
                 utm_campaign, utm_content, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (draft_id, tracking_id, dest_url, utm_source, utm_medium,
             utm_campaign, utm_content, _now()),
        )
        conn.commit()
    finally:
        conn.close()
    return tracking_id


def get_tracking_link(tracking_id: str) -> Optional[Dict[str, Any]]:
    conn = _get_conn()
    try:
        cur = _exec(conn, "SELECT * FROM tracking_links WHERE tracking_id = ?", (tracking_id,))
        row = cur.fetchone()
        return _row_to_dict(cur, row)
    finally:
        conn.close()


def record_click(tracking_id: str, ip_hash: Optional[str], user_agent: Optional[str]) -> None:
    conn = _get_conn()
    try:
        _exec(
            conn,
            "INSERT INTO clicks (tracking_id, ts, ip_hash, user_agent) VALUES (?, ?, ?, ?)",
            (tracking_id, _now(), ip_hash, user_agent),
        )
        conn.commit()
    finally:
        conn.close()


def record_conversion(
    type_: str, tracking_id: Optional[str], email_hash: Optional[str]
) -> None:
    conn = _get_conn()
    try:
        _exec(
            conn,
            "INSERT INTO conversions (tracking_id, type, email_hash, ts) VALUES (?, ?, ?, ?)",
            (tracking_id, type_, email_hash, _now()),
        )
        conn.commit()
    finally:
        conn.close()


# ---------- metrics ----------

def funnel_metrics() -> Dict[str, int]:
    conn = _get_conn()
    try:
        def _count(sql: str, params: tuple = ()) -> int:
            cur = _exec(conn, sql, params)
            return cur.fetchone()[0]

        return {
            "posts":           _count("SELECT COUNT(*) FROM posts"),
            "drafts_pending":  _count("SELECT COUNT(*) FROM drafts WHERE status = ?", ("pending",)),
            "drafts_approved": _count("SELECT COUNT(*) FROM drafts WHERE status = ?", ("approved",)),
            "drafts_rejected": _count("SELECT COUNT(*) FROM drafts WHERE status = ?", ("rejected",)),
            "clicks":          _count("SELECT COUNT(*) FROM clicks"),
            "signups":         _count("SELECT COUNT(*) FROM conversions WHERE type = ?", ("signup",)),
            "paid":            _count("SELECT COUNT(*) FROM conversions WHERE type = ?", ("paid",)),
        }
    finally:
        conn.close()
