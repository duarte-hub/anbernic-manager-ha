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


async def mount_smb(host: str, share: str, username: str, password: str, domain: str = "") -> MountResult:
    if not shutil.which("mount.cifs") and not shutil.which("mount"):
        return MountResult(False, "cifs-utils is not installed in this image.")
    if not host or not share:
        return MountResult(False, "SMB host and share name are required.")

    os.makedirs(MOUNT_POINT, exist_ok=True)
    if is_mounted():
        await unmount_smb()

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
    if not is_mounted():
        return MountResult(True, "not mounted")
    code, out = await _run(["umount", MOUNT_POINT])
    if code != 0:
        code, out = await _run(["umount", "-l", MOUNT_POINT])
    if code != 0:
        return MountResult(False, f"umount failed (exit {code}): {out.strip()[-500:]}")
    return MountResult(True, "unmounted")
