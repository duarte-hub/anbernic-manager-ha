# Changelog

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
