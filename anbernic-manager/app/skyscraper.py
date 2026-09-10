"""
Wraps the Skyscraper CLI binary: source resolution (SMB mount or local
bind-mount), system auto-detection/coverage scanning, config.ini
generation, and running scrape + gamelist-write jobs with the output
streamed line by line.

The two-pass flow (scrape into the cache, then separately write the
gamelist + media into the ROMs folder) and the defensive "did the source
disappear mid-run" checks mirror what was worked out and validated by
hand against a real Knulli SMB share before this app existed.
"""
from __future__ import annotations

import asyncio
import os
import re
import time
from pathlib import Path
from typing import AsyncIterator, Callable

from platforms import IGNORE_FOLDERS, guess_platform
import smb
import storage

SKYSCRAPER_BIN = "/usr/local/bin/Skyscraper"
DATA_DIR = Path("/data/skyscraper")
CONFIG_PATH = DATA_DIR / "config.ini"
ARTWORK_PATH = DATA_DIR / "artwork.xml"
DEFAULT_ARTWORK_SRC = Path(__file__).parent / "artwork-default.xml"

PROGRESS_RE = re.compile(r"^#(\d+)/(\d+)(?:,\s*\((\d+)/(\d+)\))?")
FOUND_RE = re.compile(r"found! :\)")
NOTFOUND_RE = re.compile(r"not found")
SUCCESS_RE = re.compile(r"Successfully processed games:\s*(\d+)")
SKIPPED_RE = re.compile(r"Skipped games:\s*(\d+)")
REMAINING_RE = re.compile(r"requests remaining:\s*(\d+)")


class SourceError(Exception):
    pass


def _ensure_artwork() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not ARTWORK_PATH.exists():
        ARTWORK_PATH.write_text(DEFAULT_ARTWORK_SRC.read_text())


def write_config() -> None:
    """(Re)write config.ini from current settings before every job, so a
    settings change always takes effect on the next run."""
    s = storage.get_settings()
    _ensure_artwork()
    lines = [
        "[main]",
        'frontend="emulationstation"',
        'relativePaths="true"',
        'unattend="true"',
        'gameListBackup="false"',
        f'regionPrios="{s["region_priority"]}"',
        "",
        "[screenscraper]",
        f'userCreds="{s["screenscraper_username"]}:{s["screenscraper_password"]}"',
        "",
    ]
    CONFIG_PATH.write_text("\n".join(lines))
    os.chmod(CONFIG_PATH, 0o600)


async def ensure_source_ready() -> Path:
    """Mount the configured SMB share. Returns the resolved source root
    (the directory that directly contains roms_subdir)."""
    s = storage.get_settings()
    result = await smb.mount_smb(
        s["smb_host"], s["smb_share"], s["smb_username"], s["smb_password"], s["smb_domain"]
    )
    if not result.ok:
        raise SourceError(result.message)
    return Path(smb.MOUNT_POINT)


def roms_root(source_root: Path) -> Path:
    s = storage.get_settings()
    return source_root / s["roms_subdir"]


def count_roms(system_dir: Path) -> int:
    n = 0
    try:
        for entry in system_dir.iterdir():
            if entry.is_file() and not entry.name.startswith(".") and entry.suffix.lower() not in (
                ".xml", ".txt", ".cfg"
            ) and "gamelist" not in entry.name:
                n += 1
    except FileNotFoundError:
        return 0
    return n


def _count_gamelist_entries(system_dir: Path) -> int:
    gl = system_dir / "gamelist.xml"
    if not gl.exists():
        return 0
    try:
        text = gl.read_text(errors="replace")
    except OSError:
        return 0
    return text.count("<game>")


def scan_systems() -> list[dict]:
    """Synchronous scan -- call only after ensure_source_ready() has run
    for the current request/job."""
    root = roms_root(Path(smb.MOUNT_POINT))
    overrides = storage.get_overrides()
    out = []
    if not root.is_dir():
        return out
    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or entry.name.lower() in IGNORE_FOLDERS or entry.name.startswith("."):
            continue
        rom_count = count_roms(entry)
        if rom_count == 0:
            continue
        override = overrides.get(entry.name)
        platform = (override or {}).get("platform") or guess_platform(entry.name)
        enabled = True if override is None else override.get("enabled", True)
        out.append({
            "folder": entry.name,
            "path": str(entry),
            "platform": platform,
            "enabled": enabled,
            "rom_count": rom_count,
            "gamelist_entries": _count_gamelist_entries(entry),
            "has_gamelist": (entry / "gamelist.xml").exists(),
        })
    return out


async def _stream_command(cmd: list[str], env: dict, on_line: Callable[[str], None]) -> str:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=env,
    )
    assert proc.stdout is not None
    buf = ""
    async for raw in proc.stdout:
        line = raw.decode(errors="replace").rstrip("\n")
        buf += line + "\n"
        on_line(line)
    await proc.wait()
    return buf


def _env() -> dict:
    env = dict(os.environ)
    env["HOME"] = str(DATA_DIR)
    env["QT_QPA_PLATFORM"] = "offscreen"
    return env


async def run_gamelist_pass(system_dir: Path, platform: str, on_line: Callable[[str], None]) -> str:
    """Rewrite gamelist.xml + media from the already-cached scrape data,
    without touching the network. Used both as pass 2 of a normal job and
    as the standalone 'fix a gamelist EmulationStation clobbered' action."""
    write_config()
    cmd = [
        SKYSCRAPER_BIN, "-c", str(CONFIG_PATH), "-p", platform,
        "-i", str(system_dir), "-a", str(ARTWORK_PATH),
        "-g", str(system_dir), "-o", str(system_dir / "media"),
        "--flags", "unattend,relative",
    ]
    return await _stream_command(cmd, _env(), on_line)


async def run_scrape_pass(system_dir: Path, platform: str, only_missing: bool, unpack: bool,
                           on_line: Callable[[str], None]) -> str:
    write_config()
    flags = "unattend,relative"
    if only_missing:
        flags = "onlymissing," + flags
    if unpack:
        flags += ",unpack"
    cmd = [
        SKYSCRAPER_BIN, "-c", str(CONFIG_PATH), "-p", platform,
        "-i", str(system_dir), "-a", str(ARTWORK_PATH),
        "-s", "screenscraper", "--flags", flags,
    ]
    return await _stream_command(cmd, _env(), on_line)


def parse_summary(log_text: str) -> dict:
    found = len(FOUND_RE.findall(log_text))
    not_found = len(NOTFOUND_RE.findall(log_text))
    m = SUCCESS_RE.findall(log_text)
    processed = int(m[-1]) if m else None
    m = SKIPPED_RE.findall(log_text)
    skipped = int(m[-1]) if m else None
    m = REMAINING_RE.findall(log_text)
    requests_remaining = int(m[-1]) if m else None
    return {
        "found": found,
        "not_found": not_found,
        "processed": processed,
        "skipped": skipped,
        "requests_remaining": requests_remaining,
    }


async def test_screenscraper_login() -> dict:
    """Run a real (network-touching) Skyscraper invocation against an
    empty temp folder just to capture its login/quota banner -- there's no
    separate lightweight endpoint to hit without the app's own registered
    ScreenScraper devid/devpassword, which Skyscraper itself owns."""
    write_config()
    tmp = DATA_DIR / "_login_test"
    tmp.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    cmd = [
        SKYSCRAPER_BIN, "-c", str(CONFIG_PATH), "-p", "atari2600",
        "-i", str(tmp), "-a", str(ARTWORK_PATH), "-s", "screenscraper",
        "--flags", "unattend",
    ]
    text = await _stream_command(cmd, _env(), lines.append)
    ok = "Fetching limits for user" in text and "Couldn't log in" not in text and "Erreur de login" not in text
    threads = None
    m = re.search(r"Setting threads to (\d+)", text)
    if m:
        threads = int(m.group(1))
    return {"ok": ok, "threads": threads, "raw": text[-1500:]}
