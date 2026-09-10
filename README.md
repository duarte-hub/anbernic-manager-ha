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

- Points at your ROM library over **SMB** -- it mounts the share itself
  (`mount -t cifs`), no host-side bind mount needed.
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
4. **Turn off "Protection mode"** for this app (Info tab) -- required for
   the next step to actually take effect; Supervisor silently ignores
   `privileged`/`apparmor` settings on a protected app.
5. Open it from the HA sidebar (Ingress panel).
6. In its **Settings** tab, enter your ScreenScraper username/password and
   your SMB host/share/credentials.

The app ships with `privileged: [SYS_ADMIN, DAC_READ_SEARCH]` and
`apparmor: false` in `config.yaml` so it can mount an SMB share itself
(`mount -t cifs` needs `SYS_ADMIN` for the mount syscall itself, and
`DAC_READ_SEARCH` plus the AppArmor exception for it to drop privileges
afterwards -- without the AppArmor exception it fails with `mount failed
(exit 2): Unable to apply new capability set.`). All three require
Protection mode to be off, as above.

## Installing standalone (docker-compose)

Works next to any Home Assistant install, or with none at all.

```bash
git clone <this repo> anbernic-manager-ha
cd anbernic-manager-ha
docker compose up -d --build
```

Open `http://<docker-host>:8099` and fill in your ScreenScraper and SMB
details in the Settings tab -- `docker-compose.yml` already grants
`SYS_ADMIN` + the AppArmor exception the container needs to mount CIFS
itself, so this works out of the box.

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
