"""
Tiny SQLite-backed storage for settings, per-folder platform overrides and
job history. Deliberately dependency-free (stdlib sqlite3) -- this app has
one user and modest data volumes, so a full ORM/migration framework would
be overkill.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

DB_PATH = Path("/data/anbernic-manager.db")

_lock = threading.Lock()

DEFAULT_SETTINGS: dict[str, Any] = {
    "screenscraper_username": "",
    "screenscraper_password": "",
    "source_type": "local",          # "local" | "smb"
    "local_path": "/share/anbernic",
    "smb_host": "",
    "smb_share": "",
    "smb_username": "",
    "smb_password": "",
    "smb_domain": "",
    "roms_subdir": "roms",
    "region_priority": "us,wor,eu,ss,uk,jp,au",
    "unpack": True,
    "only_missing_default": True,
}

SECRET_KEYS = {"screenscraper_password", "smb_password"}


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _lock, _connect() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS settings ("
            " key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS system_overrides ("
            " folder TEXT PRIMARY KEY, platform TEXT, enabled INTEGER NOT NULL DEFAULT 1)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS jobs ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " started_at REAL NOT NULL,"
            " finished_at REAL,"
            " status TEXT NOT NULL,"
            " systems_json TEXT NOT NULL,"
            " summary_json TEXT,"
            " log TEXT NOT NULL DEFAULT '')"
        )
        conn.commit()


def get_settings() -> dict[str, Any]:
    with _lock, _connect() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    stored = {r["key"]: json.loads(r["value"]) for r in rows}
    merged = dict(DEFAULT_SETTINGS)
    merged.update(stored)
    return merged


def get_settings_masked() -> dict[str, Any]:
    """Settings for the UI: secret values replaced with a boolean 'is_set'."""
    s = get_settings()
    out = dict(s)
    for key in SECRET_KEYS:
        out[key] = ""
        out[f"{key}_is_set"] = bool(s.get(key))
    return out


def update_settings(patch: dict[str, Any]) -> None:
    allowed = set(DEFAULT_SETTINGS.keys())
    with _lock, _connect() as conn:
        for key, value in patch.items():
            if key not in allowed:
                continue
            # Never overwrite a stored secret with a blank value coming from
            # a masked form re-submit.
            if key in SECRET_KEYS and value in ("", None):
                continue
            conn.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)),
            )
        conn.commit()


def get_overrides() -> dict[str, dict[str, Any]]:
    with _lock, _connect() as conn:
        rows = conn.execute("SELECT folder, platform, enabled FROM system_overrides").fetchall()
    return {r["folder"]: {"platform": r["platform"], "enabled": bool(r["enabled"])} for r in rows}


def set_override(folder: str, platform: str | None, enabled: bool = True) -> None:
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO system_overrides(folder, platform, enabled) VALUES (?, ?, ?) "
            "ON CONFLICT(folder) DO UPDATE SET platform=excluded.platform, enabled=excluded.enabled",
            (folder, platform, int(enabled)),
        )
        conn.commit()


def create_job(systems: list[dict[str, Any]]) -> int:
    with _lock, _connect() as conn:
        cur = conn.execute(
            "INSERT INTO jobs(started_at, status, systems_json, log) VALUES (?, ?, ?, '')",
            (time.time(), "running", json.dumps(systems)),
        )
        conn.commit()
        return int(cur.lastrowid)


def append_job_log(job_id: int, text: str) -> None:
    with _lock, _connect() as conn:
        conn.execute(
            "UPDATE jobs SET log = log || ? WHERE id = ?", (text, job_id)
        )
        conn.commit()


def finish_job(job_id: int, status: str, summary: dict[str, Any]) -> None:
    with _lock, _connect() as conn:
        conn.execute(
            "UPDATE jobs SET status=?, finished_at=?, summary_json=? WHERE id=?",
            (status, time.time(), json.dumps(summary), job_id),
        )
        conn.commit()


def list_jobs(limit: int = 50) -> list[dict[str, Any]]:
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT id, started_at, finished_at, status, systems_json, summary_json "
            "FROM jobs ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    out = []
    for r in rows:
        out.append({
            "id": r["id"],
            "started_at": r["started_at"],
            "finished_at": r["finished_at"],
            "status": r["status"],
            "systems": json.loads(r["systems_json"]),
            "summary": json.loads(r["summary_json"]) if r["summary_json"] else None,
        })
    return out


def get_job(job_id: int) -> dict[str, Any] | None:
    with _lock, _connect() as conn:
        r = conn.execute(
            "SELECT id, started_at, finished_at, status, systems_json, summary_json, log "
            "FROM jobs WHERE id=?",
            (job_id,),
        ).fetchone()
    if not r:
        return None
    return {
        "id": r["id"],
        "started_at": r["started_at"],
        "finished_at": r["finished_at"],
        "status": r["status"],
        "systems": json.loads(r["systems_json"]),
        "summary": json.loads(r["summary_json"]) if r["summary_json"] else None,
        "log": r["log"],
    }
