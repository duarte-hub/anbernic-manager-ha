# Anbernic Manager

A small self-hosted app that scrapes game metadata and artwork (box art,
screenshots, marquees) for your Anbernic / Knulli / EmulationStation ROM
library from [ScreenScraper](https://www.screenscraper.fr/), using the
[Skyscraper](https://github.com/Gemba/skyscraper) CLI under the hood, with
a web UI instead of the terminal.

It runs in Docker, either as a Home Assistant Supervisor **app** (the
current name for what used to be called an "add-on") with a panel in the
HA sidebar, or standalone next to any Home Assistant install (or with no
Home Assistant at all) via `docker compose`.

This is a v1 focused on the scraper. A ROM library browser, per-game
manual re-scrape, and duplicate/verification tools are natural next steps
but not built yet.

## What it does

- Points at your ROM library either over **SMB** (it mounts the share
  itself) or a **local path** you bind into the container.
- Scans `roms/<system>/` folders, guesses each one's Skyscraper platform
  code, and shows ROM count vs. already-scraped count per system -- you
  confirm/override the platform mapping before running anything.
- Runs Skyscraper's two-pass flow (scrape into its cache, then write
  `gamelist.xml` + media) per system, unattended, with relative paths so
  the result is ready to use on the handheld as-is.
- Streams the live log and progress to the browser over a WebSocket, and
  keeps a history of past runs.
- One-click **"Rewrite gamelist"**: EmulationStation/Knulli has a habit of
  saving its own (empty) `gamelist.xml` over a freshly-scraped one the
  first time you do "Update Gamelists" after a scrape, if ES had already
  loaded that system before the scrape finished. This button re-writes it
  from Skyscraper's local cache in a couple of seconds, no network calls,
  no data lost.

## Installing as a Home Assistant app (Supervisor)

Requires Home Assistant **OS** or **Supervised** (this route isn't
available on Container/Core installs -- use docker-compose instead).

1. Settings → Add-ons → Add-on Store → ⋮ (top right) → Repositories.
2. Add this repository's URL.
3. Find **Anbernic Manager** in the store, install it, start it.
4. Open it from the HA sidebar (Ingress panel).
5. In its **Settings** tab, enter your ScreenScraper username/password and
   point it at your ROM source (see below).

By default the app ships with `full_access: true` in `config.yaml` so it
can mount an SMB share itself (`mount -t cifs`, which needs the
`SYS_ADMIN` capability plus an AppArmor exception -- `full_access` is the
standard, documented way to get both from a Supervisor app). If you'd
rather not grant that:

- Remove `full_access: true` from `anbernic-manager/config.yaml`.
- Use **local path** mode instead of SMB: copy or already-mount your ROMs
  under Home Assistant's own `share` or `media` area (Settings → System →
  Storage, for a USB-attached SD card reader on the HA host), then set
  the source path in the app to the matching `/share/...` or
  `/media/...` path -- Supervisor apps can only reach paths under those
  specific mapped folders, not arbitrary host paths.

## Installing standalone (docker-compose)

Works next to any Home Assistant install, or with none at all.

```bash
git clone <this repo> anbernic-manager-ha
cd anbernic-manager-ha
cp .env.example .env   # only needed for local-path mode, see below
docker compose up -d --build
```

Open `http://<docker-host>:8099`.

- **Local path mode**: set `ROMS_HOST_PATH` in `.env` to wherever your
  ROMs already live on the Docker host (a mounted SD card, an existing
  SMB/NFS mount, a plain folder) -- docker-compose can bind-mount any
  host path, unlike a Supervisor app. It's bound to `/share/anbernic`
  inside the container by default; set that same path as "local_path" in
  the app's Settings tab.
- **SMB mode**: works out of the box (`docker-compose.yml` already grants
  `SYS_ADMIN` + the AppArmor exception needed for the container to mount
  CIFS itself) -- just fill in host/share/username/password in Settings.

## Notes

- ScreenScraper enforces a per-account request quota and thread limit
  (an anonymous/basic account is capped low and single-threaded); a job
  covering a large library can take hours. The UI shows live progress and
  the job continues in the background even if you close the browser tab
  -- come back and check the History tab.
- The Skyscraper cache and its config persist in the app's `/data` volume
  across restarts and re-scrapes, so re-running a job only fetches what's
  actually missing (with "Only fetch missing" checked, which is the
  default).
- Credentials are stored as plain config on that persistent volume, as is
  normal for a self-hosted home-lab tool -- use a dedicated ScreenScraper
  account if that's a concern.
