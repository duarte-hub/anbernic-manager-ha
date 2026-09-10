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
import re
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


class CollapseDuplicateSlashes:
    """Home Assistant Ingress requests paths with a doubled leading slash
    (e.g. `//` for the root page, `//ws/jobs/3` for the jobs WebSocket) --
    Starlette treats that as distinct from the single-slash route and
    404s/403s it. This is raw ASGI (not @app.middleware("http")) because
    that decorator only wraps HTTP requests -- WebSocket upgrades bypass
    it entirely, so the jobs WebSocket kept getting rejected even after
    the HTTP version of this fix landed."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            path = scope.get("path", "")
            collapsed = re.sub(r"/{2,}", "/", path)
            if collapsed != path:
                scope["path"] = collapsed
        await self.app(scope, receive, send)


app.add_middleware(CollapseDuplicateSlashes)


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


# Linux capability bit -> name (see capability(7)), just enough to decode
# the hex masks in /proc/self/status for the diagnostic log below.
_CAPABILITY_NAMES = {
    0: "CHOWN", 1: "DAC_OVERRIDE", 2: "DAC_READ_SEARCH", 3: "FOWNER",
    4: "FSETID", 5: "KILL", 6: "SETGID", 7: "SETUID", 8: "SETPCAP",
    9: "LINUX_IMMUTABLE", 10: "NET_BIND_SERVICE", 11: "NET_BROADCAST",
    12: "NET_ADMIN", 13: "NET_RAW", 14: "IPC_LOCK", 15: "IPC_OWNER",
    16: "SYS_MODULE", 17: "SYS_RAWIO", 18: "SYS_CHROOT", 19: "SYS_PTRACE",
    20: "SYS_PACCT", 21: "SYS_ADMIN", 22: "SYS_BOOT", 23: "SYS_NICE",
    24: "SYS_RESOURCE", 25: "SYS_TIME", 26: "SYS_TTY_CONFIG", 27: "MKNOD",
    28: "LEASE", 29: "AUDIT_WRITE", 30: "AUDIT_CONTROL", 31: "SETFCAP",
    32: "MAC_OVERRIDE", 33: "MAC_ADMIN", 34: "SYSLOG", 35: "WAKE_ALARM",
    36: "BLOCK_SUSPEND", 37: "AUDIT_READ", 38: "PERFMON", 39: "BPF",
    40: "CHECKPOINT_RESTORE",
}


def _decode_cap_mask(hex_mask: str) -> list[str]:
    bits = int(hex_mask, 16)
    return [name for bit, name in _CAPABILITY_NAMES.items() if bits & (1 << bit)]


def _log_security_context() -> None:
    """One-time diagnostic: log the container's actual Linux capabilities
    and AppArmor confinement, since config.yaml options like full_access
    / apparmor / Protection mode don't reliably tell you what a given
    Supervisor version actually applied -- this reads it straight from
    the kernel so it can be confirmed from the app's own log."""
    try:
        status = Path("/proc/self/status").read_text()
        caps = {}
        for line in status.splitlines():
            if line.startswith(("CapInh:", "CapPrm:", "CapEff:", "CapBnd:", "CapAmb:")):
                key, value = line.split(":")
                caps[key.strip()] = value.strip()
        for key, value in caps.items():
            log.info("security: %s=%s -> %s", key, value, _decode_cap_mask(value))
    except OSError as e:
        log.warning("security: could not read /proc/self/status: %s", e)

    try:
        profile = Path("/proc/self/attr/current").read_text().strip()
        log.info("security: apparmor profile = %r", profile or "(empty/unconfined)")
    except OSError as e:
        log.info("security: could not read apparmor profile (%s) -- likely no AppArmor on this host", e)


@app.on_event("startup")
async def on_startup() -> None:
    _log_security_context()
    storage.init_db()
    _load_ha_options()
    skyscraper.write_config()


# ---------- settings ----------

class SettingsIn(BaseModel):
    screenscraper_username: str | None = None
    screenscraper_password: str | None = None
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


@app.get("/api/jobs/current")
async def get_current_job() -> dict[str, Any]:
    """So the page can reconnect to an already-running job after a reload
    or reopening the Ingress panel, instead of only being told 'a job is
    already running' with no way to see its progress."""
    return {"job_id": jobs.current_job_id() if jobs.is_running() else None}


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
