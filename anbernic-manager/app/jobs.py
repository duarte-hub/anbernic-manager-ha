"""
Background job runner. One scrape job runs at a time (Skyscraper isn't
designed for concurrent runs against the same on-disk cache); each job
scrapes then writes the gamelist for one or more systems in sequence,
broadcasting progress lines to any connected WebSocket clients.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import skyscraper
import storage

_current_job_id: int | None = None
_current_task: asyncio.Task | None = None
_subscribers: dict[int, set[asyncio.Queue]] = {}


def current_job_id() -> int | None:
    return _current_job_id


def is_running() -> bool:
    return _current_task is not None and not _current_task.done()


def subscribe(job_id: int) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue()
    _subscribers.setdefault(job_id, set()).add(q)
    return q


def unsubscribe(job_id: int, q: asyncio.Queue) -> None:
    _subscribers.get(job_id, set()).discard(q)


def _broadcast(job_id: int, event: dict[str, Any]) -> None:
    for q in list(_subscribers.get(job_id, ())):
        q.put_nowait(event)


async def start_job(systems: list[dict], only_missing: bool, unpack: bool) -> int:
    if is_running():
        raise RuntimeError("A scrape job is already running.")
    job_id = storage.create_job(systems)
    global _current_job_id, _current_task
    _current_job_id = job_id
    _current_task = asyncio.create_task(_run_job(job_id, systems, only_missing, unpack))
    return job_id


async def cancel_job(job_id: int) -> bool:
    if job_id != _current_job_id or not is_running():
        return False
    assert _current_task is not None
    _current_task.cancel()
    return True


def _log_line(job_id: int, line: str) -> None:
    storage.append_job_log(job_id, line + "\n")
    _broadcast(job_id, {"type": "line", "text": line})


async def _run_job(job_id: int, systems: list[dict], only_missing: bool, unpack: bool) -> None:
    per_system: dict[str, dict] = {}
    overall_status = "completed"
    try:
        _log_line(job_id, f"=== job {job_id} starting: {len(systems)} system(s) ===")
        try:
            source_root = await skyscraper.ensure_source_ready()
        except skyscraper.SourceError as e:
            _log_line(job_id, f"!!! source not ready: {e}")
            storage.finish_job(job_id, "failed", {"error": str(e)})
            _broadcast(job_id, {"type": "done", "status": "failed"})
            return

        for sys in systems:
            folder = sys["folder"]
            platform = sys["platform"]
            system_dir = skyscraper.roms_root(source_root) / folder

            if not system_dir.is_dir():
                _log_line(job_id, f"!!! {system_dir} is missing -- source may have dropped, stopping.")
                overall_status = "failed"
                per_system[folder] = {"error": "path missing"}
                break

            rom_filename = sys.get("rom_filename")
            _broadcast(job_id, {"type": "system_start", "folder": folder})
            _log_line(job_id, f"--- {folder} ({platform}): scraping"
                              f"{f' {rom_filename}' if rom_filename else ''} ---")
            scrape_log = await skyscraper.run_scrape_pass(
                system_dir, platform, only_missing, unpack,
                lambda line, f=folder: (_log_line(job_id, line), _emit_progress(job_id, f, line)),
                rom_filename=rom_filename,
            )

            if not system_dir.is_dir():
                _log_line(job_id, f"!!! {system_dir} vanished during scrape -- stopping before writing gamelist.")
                overall_status = "failed"
                per_system[folder] = {"error": "path vanished mid-scrape"}
                break

            _log_line(job_id, f"--- {folder} ({platform}): writing gamelist ---")
            gl_log = await skyscraper.run_gamelist_pass(
                system_dir, platform, lambda line: _log_line(job_id, line)
            )
            summary = skyscraper.parse_summary(scrape_log + gl_log)
            per_system[folder] = summary
            _broadcast(job_id, {"type": "system_done", "folder": folder, "summary": summary})
            _log_line(job_id, f"--- {folder}: done ({summary}) ---")

        _log_line(job_id, f"=== job {job_id} {overall_status} ===")
    except asyncio.CancelledError:
        overall_status = "cancelled"
        _log_line(job_id, f"=== job {job_id} cancelled ===")
        raise
    finally:
        storage.finish_job(job_id, overall_status, {"systems": per_system})
        _broadcast(job_id, {"type": "done", "status": overall_status})
        global _current_job_id, _current_task
        _current_job_id = None
        _current_task = None


def _emit_progress(job_id: int, folder: str, line: str) -> None:
    m = skyscraper.PROGRESS_RE.match(line)
    if m:
        _broadcast(job_id, {
            "type": "progress", "folder": folder,
            "current": int(m.group(1)), "total": int(m.group(2)),
        })
