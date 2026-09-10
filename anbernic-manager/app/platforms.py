"""
Skyscraper platform codes and folder auto-mapping.

PLATFORM_CODES is the exact list of `-p` values supported by Skyscraper
3.20.4 (Gemba/skyscraper fork), taken directly from a real `Skyscraper
--help` run against the compiled binary -- not from documentation, which
in the upstream project has drifted from what the current code actually
accepts.

FOLDER_TO_PLATFORM maps common Knulli / Batocera / RetroPie / ES-DE ROM
folder names to a Skyscraper platform code, for auto-detecting systems
under a roms/ tree. It's a best-effort guess: the UI always lets the user
confirm or override it per folder before a scrape runs.
"""

PLATFORM_CODES = sorted([
    "3do", "3ds", "actionmax", "ags", "amiga", "amstradcpc", "apple2", "apple2gs",
    "arcade", "arcadia", "arduboy", "astrocade", "atari2600", "atari5200",
    "atari7800", "atari800", "atarijaguar", "atarijaguarcd", "atarilynx",
    "atarist", "atomiswave", "bbcmicro", "c128", "c64", "cd32", "cdi", "cdtv",
    "channelf", "coco", "coleco", "crvision", "daphne", "dragon32", "dreamcast",
    "easyrpg", "fba", "fds", "fm7", "fmtowns", "gameandwatch", "gamecom",
    "gamegear", "gb", "gba", "gbc", "gc", "gmaster", "intellivision", "j2me",
    "love", "macintosh", "mame", "mame-advmame", "mame-libretro",
    "mame-mame4all", "mastersystem", "megadrive", "megaduck", "moto", "msx",
    "msx2", "n64", "n64dd", "naomi", "naomi2", "nds", "neogeo", "neogeocd",
    "nes", "ngp", "ngpc", "openbor", "oric", "palm", "pc", "pc88", "pc98",
    "pcengine", "pcenginecd", "pcfx", "pico8", "plus4", "pokemini", "ports",
    "ps2", "ps3", "ps4", "ps5", "psp", "psvita", "psx", "pv1000", "samcoupe",
    "saturn", "scummvm", "scv", "sega32x", "segacd", "sg-1000", "snes",
    "solarus", "steam", "stratagus", "supervision", "switch", "symbian",
    "ti99", "tic80", "trs-80", "vectrex", "vic20", "videopac", "vircon32",
    "virtualboy", "vsmile", "wii", "wiiu", "wonderswan", "wonderswancolor",
    "x1", "x68000", "xbox", "xbox360", "zmachine", "zx81", "zxspectrum",
])

# folder name (as seen under roms/) -> Skyscraper platform code
FOLDER_TO_PLATFORM = {
    "atari2600": "atari2600", "atari5200": "atari5200", "atari7800": "atari7800",
    "atari800": "atari800", "atarist": "atarist", "atarilynx": "atarilynx",
    "lynx": "atarilynx", "jaguar": "atarijaguar", "atomiswave": "atomiswave",
    "c64": "c64", "c128": "c128", "vic20": "vic20", "amiga500": "amiga",
    "amiga1200": "amiga", "amigacd32": "cd32", "amigacdtv": "cdtv",
    "amstradcpc": "amstradcpc", "apple2": "apple2", "apple2gs": "apple2gs",
    "coco": "coco", "colecovision": "coleco", "channelf": "channelf",
    "crvision": "crvision", "dreamcast": "dreamcast", "easyrpg": "easyrpg",
    "fds": "fds", "fbneo": "fba", "fba": "fba", "gameandwatch": "gameandwatch",
    "gamecom": "gamecom", "gamegear": "gamegear", "gb": "gb", "gb2players": "gb",
    "gba": "gba", "gbc": "gbc", "gbc2players": "gbc", "gc": "gc",
    "gamecube": "gc", "gmaster": "gmaster", "intellivision": "intellivision",
    "mame": "mame", "mastersystem": "mastersystem", "megadrive": "megadrive",
    "genesis": "megadrive", "msx1": "msx", "msx": "msx", "msx2": "msx2",
    "n64": "n64", "n64dd": "n64dd", "naomi": "naomi", "naomi2": "naomi2",
    "nds": "nds", "neogeo": "neogeo", "neogeocd": "neogeocd", "nes": "nes",
    "fds_nes": "fds", "ngp": "ngp", "ngpc": "ngpc", "openbor": "openbor",
    "oric": "oric", "pc": "pc", "dos": "pc", "pcengine": "pcengine",
    "pcenginecd": "pcenginecd", "pcfx": "pcfx", "pico8": "pico8",
  "plus4": "plus4", "cplus4": "plus4", "pokemini": "pokemini",
    "pokemon-mini": "pokemini", "ports": "ports", "ps2": "ps2", "ps3": "ps3",
    "psp": "psp", "psvita": "psvita", "psx": "psx", "saturn": "saturn",
    "scummvm": "scummvm", "sega32x": "sega32x", "32x": "sega32x",
    "segacd": "segacd", "sg1000": "sg-1000", "sg-1000": "sg-1000",
    "snes": "snes", "supervision": "supervision", "switch": "switch",
    "ti99": "ti99", "tic80": "tic80", "trs-80": "trs-80", "vectrex": "vectrex",
    "videopac": "videopac", "virtualboy": "virtualboy", "vsmile": "vsmile",
    "wii": "wii", "wiiu": "wiiu", "wswan": "wonderswan",
    "wonderswan": "wonderswan", "wswanc": "wonderswancolor",
    "wonderswancolor": "wonderswancolor", "x68000": "x68000", "xbox": "xbox",
    "xbox360": "xbox360", "zx81": "zx81", "zxspectrum": "zxspectrum",
    "zxspectrum48": "zxspectrum",
}

# Folders that commonly show up under roms/ but have no sensible Skyscraper
# platform (BIOS/tool folders, emulator-internal dirs) -- excluded from
# auto-detection entirely rather than guessed at.
IGNORE_FOLDERS = {
    "bios", "cheats", "decorations", "emulators", "themes", "screenshots",
    "saves", "system", "_replaced", "media", "images",
}


def guess_platform(folder_name: str) -> str | None:
    """Best-effort platform guess for a roms/<folder_name> directory."""
    key = folder_name.strip().lower()
    if key in FOLDER_TO_PLATFORM:
        return FOLDER_TO_PLATFORM[key]
    if key in PLATFORM_CODES:
        return key
    return None
