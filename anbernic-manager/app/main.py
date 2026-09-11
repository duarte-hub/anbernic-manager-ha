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
import shutil
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

import deviceconfig
import jobs
import library
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
    device_config_path: str | None = None
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
    # force=True: this button exists to validate the currently-typed
    # credentials specifically, so it must always do a genuine fresh
    # mount rather than short-circuiting on an already-live one.
    result = await smb.mount_smb(
        s["smb_host"], s["smb_share"], s["smb_username"], s["smb_password"], s["smb_domain"], force=True
    )
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


def _validate_path_segment(name: str, what: str = "name") -> None:
    """folder/filename path params get concatenated onto a real
    filesystem path (system_dir / name, and further deletes/writes from
    there) -- reject anything that isn't plainly one path segment, since
    "{folder}" alone (e.g. folder="..") is enough to escape roms_subdir
    to the share root without ever containing a literal "/"."""
    if not name or "/" in name or "\\" in name or name in (".", "..") or name.startswith("-"):
        raise HTTPException(400, f"Invalid {what}.")


async def _system_dir(folder: str) -> Path:
    _validate_path_segment(folder, "folder name")
    try:
        source_root = await skyscraper.ensure_source_ready()
    except skyscraper.SourceError as e:
        raise HTTPException(400, str(e))
    system_dir = skyscraper.roms_root(source_root) / folder
    if not system_dir.is_dir():
        raise HTTPException(404, f"{system_dir} not found.")
    return system_dir


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
    _validate_path_segment(folder, "folder name")
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


@app.delete("/api/systems/{folder}")
async def delete_system_folder(folder: str) -> dict[str, Any]:
    """Deletes a system folder from the share -- only ever an *empty*
    one (0 ROMs, recomputed here rather than trusting the client's
    cached count), since this removes it and anything already scraped
    into it permanently."""
    system_dir = await _system_dir(folder)
    rom_count = skyscraper.count_roms(system_dir)
    if rom_count != 0:
        raise HTTPException(400, f"'{folder}' has {rom_count} ROM(s) -- refusing to delete a non-empty folder.")
    shutil.rmtree(system_dir)
    return {"ok": True}


# ---------- library (per-game ROM browser) ----------

@app.get("/api/systems/{folder}/games")
async def get_games(folder: str) -> dict[str, Any]:
    system_dir = await _system_dir(folder)
    return {"games": library.list_games(system_dir)}


@app.get("/api/systems/{folder}/media")
async def get_game_media(folder: str, path: str) -> FileResponse:
    system_dir = await _system_dir(folder)
    media_path = library.resolve_media_path(system_dir, path)
    if media_path is None:
        raise HTTPException(404, "Media file not found.")
    return FileResponse(str(media_path))


@app.delete("/api/systems/{folder}/games/{filename}")
async def delete_system_game(folder: str, filename: str) -> dict[str, Any]:
    """Deletes one ROM (plus its scraped media and gamelist entry, if
    any) -- permanent, unlike disabling a device-config key."""
    _validate_path_segment(filename, "filename")
    system_dir = await _system_dir(folder)
    if not (system_dir / filename).is_file():
        raise HTTPException(404, f"'{filename}' not found in '{folder}'.")
    library.delete_game(system_dir, filename)
    return {"ok": True}


# ---------- device config (Knulli/batocera.conf) ----------

class DeviceConfigSet(BaseModel):
    key: str
    value: str
    enabled: bool = True


async def _read_device_config() -> tuple[Path, list[dict[str, Any]]]:
    try:
        source_root = await skyscraper.ensure_source_ready()
    except skyscraper.SourceError as e:
        raise HTTPException(400, str(e))
    # device_config_path is free-text (Settings tab), so a stray "../.."
    # in it could otherwise point this read/write anywhere on the
    # container's filesystem -- confine it to the mounted share.
    root = source_root.resolve()
    path = (source_root / storage.get_settings()["device_config_path"]).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        raise HTTPException(400, "Device config file path escapes the share root -- check the setting.")
    if not path.is_file():
        raise HTTPException(404, f"{path} not found on the share.")
    return path, deviceconfig.parse(path.read_text(errors="replace"))


@app.get("/api/device-config")
async def get_device_config() -> dict[str, Any]:
    path, entries = await _read_device_config()
    return {"path": str(path), "entries": deviceconfig.public_entries(entries)}


@app.put("/api/device-config")
async def set_device_config(body: DeviceConfigSet) -> dict[str, Any]:
    path, entries = await _read_device_config()
    entries = deviceconfig.upsert(entries, body.key, body.value, body.enabled)
    path.write_text(deviceconfig.serialize(entries))
    return {"entries": deviceconfig.public_entries(entries)}


@app.delete("/api/device-config/{key}")
async def disable_device_config(key: str) -> dict[str, Any]:
    """Disables (comments out) a key rather than deleting the line --
    mirrors the device's own semantics for a disabled setting, and keeps
    the previously-set value on the line in case it's re-enabled later."""
    path, entries = await _read_device_config()
    if deviceconfig.find(entries, key) is None:
        raise HTTPException(404, f"'{key}' not found in {path}.")
    entries = deviceconfig.set_enabled(entries, key, False)
    path.write_text(deviceconfig.serialize(entries))
    return {"entries": deviceconfig.public_entries(entries)}


# ---------- jobs ----------

class JobSystemIn(BaseModel):
    folder: str
    platform: str
    rom_filename: str | None = None

    @field_validator("rom_filename")
    @classmethod
    def _validate_rom_filename(cls, v: str | None) -> str | None:
        # rom_filename is appended to a Skyscraper subprocess argv (see
        # skyscraper.run_scrape_pass) -- reject anything that could be
        # mistaken for a flag or escape the system folder, at the API
        # boundary rather than relying only on the subprocess-layer check.
        if v is not None and (v.startswith("-") or "/" in v or v in (".", "..")):
            raise ValueError(f"invalid rom_filename: {v!r}")
        return v


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
