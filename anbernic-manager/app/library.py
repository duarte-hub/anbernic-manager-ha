"""
Per-game ROM library operations for one system folder at a time: listing
games (ROM file cross-referenced with its gamelist.xml entry, if any),
resolving/serving their media files, and deleting a game (ROM + its media
+ its gamelist entry).

gamelist.xml is Skyscraper/EmulationStation-generated, not hand-edited by
users the way batocera.conf is, so unlike deviceconfig.py this reparses
and rewrites it with ElementTree rather than preserving it byte-for-byte
-- losing incidental whitespace on a rewrite is an acceptable trade-off
here, but silently corrupting a game's data would not be.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import parse as _safe_parse

GAMELIST_FILENAME = "gamelist.xml"
IGNORED_SUFFIXES = {".xml", ".txt", ".cfg"}
GAME_FIELDS = (
    "name", "desc", "image", "thumbnail", "video", "marquee",
    "rating", "releasedate", "developer", "publisher", "genre", "players",
)
MEDIA_FIELDS = ("image", "thumbnail", "video", "marquee", "fanart")


def _norm(path_text: str) -> str:
    """gamelist.xml paths are relative (Skyscraper's relativePaths=true),
    typically './filename.zip' -- normalize away the leading './'."""
    return path_text[2:] if path_text.startswith("./") else path_text


def _rom_files(system_dir: Path) -> list[Path]:
    out = []
    for entry in sorted(system_dir.iterdir()):
        if not entry.is_file() or entry.name.startswith("."):
            continue
        if entry.suffix.lower() in IGNORED_SUFFIXES or "gamelist" in entry.name.lower():
            continue
        out.append(entry)
    return out


def _load_gamelist(system_dir: Path) -> ET.ElementTree | None:
    gl = system_dir / GAMELIST_FILENAME
    if not gl.is_file():
        return None
    try:
        return _safe_parse(str(gl))
    except (ET.ParseError, DefusedXmlException):
        return None


def list_games(system_dir: Path) -> list[dict[str, Any]]:
    tree = _load_gamelist(system_dir)
    by_path: dict[str, ET.Element] = {}
    if tree is not None:
        for game in tree.getroot().findall("game"):
            path_el = game.find("path")
            if path_el is not None and path_el.text:
                by_path[_norm(path_el.text)] = game

    out = []
    for rom in _rom_files(system_dir):
        game = by_path.get(rom.name)
        entry: dict[str, Any] = {"filename": rom.name, "scraped": game is not None}
        for field in GAME_FIELDS:
            el = game.find(field) if game is not None else None
            entry[field] = el.text if el is not None and el.text else None
        out.append(entry)
    return out


def resolve_media_path(system_dir: Path, relpath: str) -> Path | None:
    """Resolves a gamelist-recorded media path to an absolute one,
    refusing to serve/delete anything that isn't actually inside
    system_dir (defends against a maliciously crafted path)."""
    system_root = system_dir.resolve()
    target = (system_dir / _norm(relpath)).resolve()
    try:
        target.relative_to(system_root)
    except ValueError:
        return None
    return target if target.is_file() else None


def delete_game(system_dir: Path, filename: str) -> None:
    """Deletes the ROM file, any media files its gamelist entry pointed
    at, and that entry itself -- leaving everything else in gamelist.xml
    untouched."""
    rom_path = system_dir / filename
    if rom_path.is_file():
        rom_path.unlink()

    tree = _load_gamelist(system_dir)
    if tree is None:
        return
    root = tree.getroot()
    for game in root.findall("game"):
        path_el = game.find("path")
        if path_el is None or _norm(path_el.text or "") != filename:
            continue
        for field in MEDIA_FIELDS:
            el = game.find(field)
            if el is not None and el.text:
                media_path = resolve_media_path(system_dir, el.text)
                if media_path is not None:
                    media_path.unlink(missing_ok=True)
        root.remove(game)
        tree.write(str(system_dir / GAMELIST_FILENAME), encoding="UTF-8", xml_declaration=True)
        break
