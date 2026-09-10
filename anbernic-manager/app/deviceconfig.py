"""
Parses and rewrites Knulli/Batocera's batocera.conf -- a flat file of
`key=value` lines (see https://wiki.batocera.org/batocera_conf_syntax).

Deliberately conservative: every line we don't recognize as a setting is
kept byte-for-byte, in its original position, so editing one key can never
alter or drop anything else in the file. Two comment conventions matter:

- `##...`  is an ordinary explanatory comment, inert.
- `#key=value` is a *disabled* setting (still parsed as that key, just
  with enabled=False) -- collapsing this into an ordinary comment would
  lose that distinction, which batocera-settings-get/set on the device
  itself also relies on.
"""
from __future__ import annotations

from typing import Any


def parse(text: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for raw in text.splitlines():
        if raw.strip() == "":
            entries.append({"type": "blank", "raw": raw})
        elif raw.startswith("##"):
            entries.append({"type": "comment", "raw": raw, "text": raw[2:].strip()})
        else:
            disabled = raw.startswith("#")
            body = raw[1:] if disabled else raw
            if "=" in body:
                key, _, value = body.partition("=")
                entries.append({
                    "type": "setting", "raw": raw,
                    "key": key, "value": value, "enabled": not disabled,
                })
            else:
                entries.append({"type": "unknown", "raw": raw})
    return entries


def serialize(entries: list[dict[str, Any]]) -> str:
    lines = []
    for e in entries:
        if e["type"] == "setting":
            prefix = "" if e["enabled"] else "#"
            lines.append(f"{prefix}{e['key']}={e['value']}")
        else:
            lines.append(e["raw"])
    text = "\n".join(lines)
    return text + "\n" if text else ""


def find(entries: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
    for e in entries:
        if e["type"] == "setting" and e["key"] == key:
            return e
    return None


def upsert(entries: list[dict[str, Any]], key: str, value: str, enabled: bool = True) -> list[dict[str, Any]]:
    """Returns a new entries list with `key` set to `value`/`enabled` --
    updating it in place if present, appending a new setting line if not."""
    existing = find(entries, key)
    if existing is not None:
        return [
            {**e, "value": value, "enabled": enabled} if e is existing else e
            for e in entries
        ]
    return [*entries, {"type": "setting", "raw": f"{key}={value}", "key": key, "value": value, "enabled": enabled}]


def set_enabled(entries: list[dict[str, Any]], key: str, enabled: bool) -> list[dict[str, Any]]:
    """Disable ("#"-comment) or re-enable an existing key without touching
    its stored value -- mirrors the device's own disable semantics rather
    than deleting the line."""
    return [
        {**e, "enabled": enabled} if e["type"] == "setting" and e["key"] == key else e
        for e in entries
    ]


def public_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Entries worth showing in the UI: settings (active or disabled) and
    the explanatory comments between them, in file order. Blank lines and
    unrecognized lines are round-trip-only, not shown."""
    return [e for e in entries if e["type"] in ("setting", "comment")]
