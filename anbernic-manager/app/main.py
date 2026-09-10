"""
Anbernic Manager -- FastAPI app.

Manages ScreenScraper scraping (via the Skyscraper CLI) for a ROM library
reached either over SMB or a locally mounted path. Runs standalone
(docker-compose) or as a Home Assistant Supervisor app/add-on -- when
running under Supervisor, /data/options.json (written by the app's
Configuration tab) is read on startup and merged into settings.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import jobs
import platforms as platforms_mod
import skyscraper
import smb
import storage

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("anbernic-manager")

app = FastAPI(title="Anbernic Manager")

STATIC_DIR = Path(__file__).parent / "static"
HA_OPTIONS_PATH = Path("/data/options.json")


def _load_ha_options() -> None:
    """If running as a Supervisor app, its Configuration tab writes
    /data/options.json -- pull screenscraper creds from there on every
    boot so that tab stays authoritative when it's used."""
    if not HA_OPTIONS_PATH.exists():
        return
    try:
        options = json.loads(HA_OPTIONS_PATH.read_text())
    except (OSError, json.JSONDecodeError) as e:
        log.warning("could not read /data/options.json: %s", e)
        return
    patch = {}
    for key in ("screenscraper_username", "screenscraper_password"):
        if options.get(key):
            patch[key] = options[key]
    if patch:
        storage.update_settings(patch)


@app.on_event("startup")
async def on_startup() -> None:
    storage.init_db()
    _load_ha_options()
    skyscraper.write_config()


# ---------- settings ----------

class SettingsIn(BaseModel):
    screenscraper_username: str | None = None
    screenscraper_password: str | None = None
    source_type: str | None = None
    local_path: str | None = None
    smb_host: str | None = None
    smb_share: str | None = None
    smb_username: str | None = None
    smb_password: str | None = None
    smb_domain: str | None = None
    roms_subdir: str | None = None
    region_priority: str | None = None
    unpack: bool | None = None
    only_missing_default: bool | None = None


@app.get("/api/settings")
async def get_settings() -> dict[str, Any]:
    return storage.get_settings_masked()


@app.post("/api/settings")
async def post_settings(body: SettingsIn) -> dict[str, Any]:
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    storage.update_settings(patch)
    skyscraper.write_config()
    return storage.get_settings_masked()


@app.post("/api/settings/test-smb")
async def test_smb() -> dict[str, Any]:
    s = storage.get_settings()
    result = await smb.mount_smb(s["smb_host"], s["smb_share"], s["smb_username"], s["smb_password"], s["smb_domain"])
    if result.ok:
        try:
            n = len(list(Path(smb.MOUNT_POINT).iterdir()))
        finally:
            await smb.unmount_smb()
        return {"ok": True, "message": f"{result.message} -- {n} entries visible."}
    return {"ok": False, "message": result.message}


@app.post("/api/settings/test-screenscraper")
async def test_screenscraper() -> dict[str, Any]:
    return await skyscraper.test_screenscraper_login()


@app.get("/api/platforms")
async def get_platforms() -> list[str]:
    return platforms_mod.PLATFORM_CODES


# ---------- systems ----------

class OverrideIn(BaseModel):
    folder: str
    platform: str | None = None
    enabled: bool = True


@app.get("/api/systems")
async def get_systems() -> dict[str, Any]:
    try:
        await skyscraper.ensure_source_ready()
    except skyscraper.SourceError as e:
        raise HTTPException(400, str(e))
    return {"systems": skyscraper.scan_systems()}


@app.post("/api/systems/override")
async def post_override(body: OverrideIn) -> dict[str, Any]:
    storage.set_override(body.folder, body.platform, body.enabled)
    return {"ok": True}


@app.post("/api/systems/{folder}/rewrite-gamelist")
async def rewrite_gamelist(folder: str) -> dict[str, Any]:
    """Fix the common EmulationStation/Knulli quirk where booting the
    handheld and updating gamelists before a system has ever been scraped
    causes ES to save its own (metadata-less) gamelist.xml over ours the
    first time. Re-running just the gamelist-write pass from the local
    Skyscraper cache fixes it in a couple of seconds, no network calls."""
    try:
        source_root = await skyscraper.ensure_source_ready()
    except skyscraper.SourceError as e:
        raise HTTPException(400, str(e))
    overrides = storage.get_overrides()
    platform = (overrides.get(folder) or {}).get("platform") or platforms_mod.guess_platform(folder)
    if not platform:
        raise HTTPException(400, f"No platform is set for '{folder}'; set one first.")
    system_dir = skyscraper.roms_root(source_root) / folder
    if not system_dir.is_dir():
        raise HTTPException(404, f"{system_dir} not found.")
    lines: list[str] = []
    log_text = await skyscraper.run_gamelist_pass(system_dir, platform, lines.append)
    return {"ok": True, "log": log_text}


# ---------- jobs ----------

class JobSystemIn(BaseModel):
    folder: str
    platform: str


class JobIn(BaseModel):
    systems: list[JobSystemIn]
    only_missing: bool = True
    unpack: bool = True


@app.post("/api/jobs")
async def post_job(body: JobIn) -> dict[str, Any]:
    if not body.systems:
        raise HTTPException(400, "No systems selected.")
    try:
        job_id = await jobs.start_job(
            [s.model_dump() for s in body.systems], body.only_missing, body.unpack
        )
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"job_id": job_id}


@app.get("/api/jobs")
async def get_jobs() -> list[dict[str, Any]]:
    return storage.list_jobs()


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: int) -> dict[str, Any]:
    j = storage.get_job(job_id)
    if not j:
        raise HTTPException(404, "job not found")
    return j


@app.post("/api/jobs/{job_id}/cancel")
async def post_cancel(job_id: int) -> dict[str, Any]:
    ok = await jobs.cancel_job(job_id)
    return {"ok": ok}


@app.websocket("/ws/jobs/{job_id}")
async def ws_job(websocket: WebSocket, job_id: int) -> None:
    await websocket.accept()
    q = jobs.subscribe(job_id)
    try:
        while True:
            event = await q.get()
            await websocket.send_json(event)
            if event.get("type") == "done":
                break
    except WebSocketDisconnect:
        pass
    finally:
        jobs.unsubscribe(job_id, q)


# ---------- static frontend ----------

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"))
