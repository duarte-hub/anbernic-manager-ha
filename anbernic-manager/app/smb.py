"""
CIFS/SMB mount helper. Requires cifs-utils in the image and either the
`SYS_ADMIN` capability or `full_access: true` in the app's config.yaml
(see README -- this is what lets a container call `mount` at all).
"""
from __future__ import annotations

import asyncio
import os
import shutil
from dataclasses import dataclass

MOUNT_POINT = "/mnt/romsource"

# Serializes actual mount/umount syscalls against the single shared
# MOUNT_POINT. Without this, concurrent requests that each call
# ensure_source_ready() -- e.g. the Library grid loading many game
# images at once -- would race each other unmounting/remounting mid-read,
# which is what caused artwork to intermittently 404 and "fix itself" on
# a retry once the mount had settled again.
_mount_lock = asyncio.Lock()


@dataclass
class MountResult:
    ok: bool
    message: str


async def _run(cmd: list[str]) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    out, _ = await proc.communicate()
    return proc.returncode or 0, out.decode(errors="replace")


def is_mounted(path: str = MOUNT_POINT) -> bool:
    return os.path.ismount(path)


async def _do_unmount() -> MountResult:
    """Actual unmount, assumes the caller already holds _mount_lock."""
    if not is_mounted():
        return MountResult(True, "not mounted")
    code, out = await _run(["umount", MOUNT_POINT])
    if code != 0:
        code, out = await _run(["umount", "-l", MOUNT_POINT])
    if code != 0:
        return MountResult(False, f"umount failed (exit {code}): {out.strip()[-500:]}")
    return MountResult(True, "unmounted")


async def mount_smb(host: str, share: str, username: str, password: str, domain: str = "",
                     force: bool = False) -> MountResult:
    """Mounts the share at MOUNT_POINT. By default this is idempotent --
    if it's already mounted, that's treated as success rather than
    tearing it down and remounting (every ordinary scan/browse/scrape
    call goes through here via ensure_source_ready(), so remounting on
    every single one is both needless and, under concurrency, unsafe).
    Pass force=True (used by the Settings "Test connection" button) to
    always do a genuine fresh mount with the current credentials."""
    if not shutil.which("mount.cifs") and not shutil.which("mount"):
        return MountResult(False, "cifs-utils is not installed in this image.")
    if not host or not share:
        return MountResult(False, "SMB host and share name are required.")

    async with _mount_lock:
        os.makedirs(MOUNT_POINT, exist_ok=True)
        if is_mounted():
            if not force:
                return MountResult(True, f"Already mounted at {MOUNT_POINT}")
            await _do_unmount()

        unc = f"//{host}/{share}".replace("//", "//", 1)
        options = f"username={username},password={password},vers=3.0,iocharset=utf8,uid=0,gid=0"
        if domain:
            options += f",domain={domain}"

        code, out = await _run(["mount", "-t", "cifs", unc, MOUNT_POINT, "-o", options])
        if code != 0:
            # Retry once against SMB2 in case the NAS/handheld doesn't speak 3.0.
            options_v2 = options.replace("vers=3.0", "vers=2.0")
            code, out = await _run(["mount", "-t", "cifs", unc, MOUNT_POINT, "-o", options_v2])
        if code != 0:
            return MountResult(False, f"mount failed (exit {code}): {out.strip()[-500:]}")
        return MountResult(True, f"Mounted {unc} at {MOUNT_POINT}")


async def unmount_smb() -> MountResult:
    async with _mount_lock:
        return await _do_unmount()
