# Changelog

## 0.2.0

- New **Delete empty folder** action per system with 0 ROMs (Systems
  tab) -- removes it from the share. Confirmed client-side and
  re-checked server-side (refuses if the folder actually has ROMs in
  it); irreversible.
- New **Device** tab: reads/writes Knulli's `system/batocera.conf`
  (configurable via a new "Device config file" setting) as an
  editable key/value table, round-tripping every other line in the
  file untouched. Disabling a key comments it out rather than
  deleting it, matching the device's own semantics. Only shows keys
  that already exist in the file, plus a form to add new ones --
  deliberately not a guided settings menu, since there's no reliable
  source for what every EmulationStation menu option's underlying key
  actually is.

## 0.1.7

- Fix the jobs WebSocket getting rejected (403) under Home Assistant
  Ingress: the double-slash-path fix from 0.1.2 only patched HTTP
  requests (`@app.middleware("http")` doesn't run for WebSocket
  upgrades at all). Replaced it with a raw ASGI middleware that
  normalizes both HTTP and WebSocket scopes.
- Add `GET /api/jobs/current` and have the page call it on load, so
  reopening the Ingress panel while a job is still running reconnects
  to it (log seeded from history) instead of only surfacing "a job is
  already running" with no way to see its progress.

## 0.1.6

- Fix "Scrape selected"/"Scrape all missing" appearing to do nothing:
  a failed job-start request (e.g. no systems selected, or a job
  already running) was thrown but never caught, so it silently failed
  instead of showing an error. Errors now show in the Systems tab.
- Removed "local path" ROM source mode entirely -- SMB is now the
  only source type, everywhere (HA app, docker-compose, settings UI).
  Dropped `source_type`/`local_path` from settings and the `share`/
  `media` folder mappings from `config.yaml`; docker-compose no longer
  needs a host bind-mount or `.env`.
- Job panel is now a small status window: a status badge (running /
  completed / failed / cancelled) plus a "Log detail" selector
  (Quiet / Normal / Verbose) that filters how much of the raw
  Skyscraper output is shown, without losing any of it -- switching
  the level re-renders from the full buffered log.

## 0.1.5

- The startup diagnostic showed `full_access: true` was not actually
  granting `SYS_ADMIN`/`DAC_READ_SEARCH` under the current Supervisor.
  Switched to explicitly requesting `privileged: [SYS_ADMIN,
  DAC_READ_SEARCH]`, which is what `mount.cifs` actually needs.
  **This (and `apparmor: false`) only take effect once this app's
  "Protection mode" toggle is switched off in its Info tab** -- with
  Protection mode on, both settings are silently ignored.
- Fix "Test connection"/"Test login" testing stale saved settings
  instead of whatever is currently typed in the form (they now save
  the form first, then run the test).

## 0.1.4

- The SMB mount capability error persists even with `full_access`,
  `apparmor: false`, and Protection mode disabled. Added a startup
  diagnostic log of the container's actual Linux capabilities
  (decoded from `/proc/self/status`) and AppArmor confinement state,
  to pin down what Supervisor is actually granting at runtime.

## 0.1.3

- Fix `mount failed (exit 2): Unable to apply new capability set` when
  mounting an SMB share: Supervisor's default AppArmor profile blocks
  the `capset` syscall `mount.cifs` needs even under `full_access`.
  Added `apparmor: false` to config.yaml.

## 0.1.2

- Fix `{"detail":"Not Found"}` on the root page specifically when
  opened via Home Assistant's Ingress: Supervisor requests the root
  page as `//` (double slash), which doesn't match FastAPI's `/`
  route. Middleware now collapses duplicate leading slashes before
  routing.

## 0.1.1

- Fix UI showing `{"detail":"Not Found"}` when opened through Home
  Assistant Ingress: static assets, API calls, and the jobs WebSocket
  now use paths relative to the current page instead of absolute
  paths, so they resolve correctly behind Ingress's per-session token
  sub-path.
- Fix add-on/repository metadata pointing at a placeholder GitHub URL.

## 0.1.0

Initial release.

- Web UI for scraping a ROM library with ScreenScraper via Skyscraper.
- Auto-detects systems under `roms/`, guesses each folder's Skyscraper
  platform code, lets you override it.
- Coverage view (ROMs vs. scraped entries) per system.
- SMB or local-path ROM source.
- One-click "rewrite gamelist" to fix EmulationStation/Knulli overwriting
  a freshly-scraped gamelist.xml with an empty one.
- Live job log over WebSocket, job history.
