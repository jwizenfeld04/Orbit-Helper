# Changelog

All notable changes to Orbit-Helper are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
How to cut a release: [RELEASING.md](RELEASING.md).

## [Unreleased]

## [1.0.1] - 2026-10-28

### Fixed

- **Home button stopped opening Orbit.** `homewatch.sh` ignored repeat Home presses by subtracting millisecond timestamps, but the TV's shell only does 32-bit arithmetic: once the millisecond clock wrapped to a negative 32-bit value (on 2026-10-27, for about 25 days at a time), every Home press looked like a repeat and was ignored. It now uses only the clock's last 9 digits and treats a wrap-around as a new press.

## [1.0.0] - 2026-10-28

The first public release.

### Added

- **Amazon Music and Alexa:** `homewatch.sh` now treats Amazon Music like Spotify. If it opens by itself, the TV goes back to Orbit's idle mode after 2 s. "Alexa, play …" plays through Alexa's own player, and its full-screen now-playing screen is sent to idle mode the same way, but only while music is playing, so other Alexa answers stay on screen.

### Changed

- Renamed from tv-input-helper to **Orbit-Helper**: `orbit_helper.py`, `orbit-helper.service`, cloned to `~/Orbit-Helper`.
- Settings come from the environment or `.env` (see `.env.example`). `TV_IP` is required: there's no built-in address. `BIND` defaults to `0.0.0.0`, `SPORTS_REMOTE_URL` to `http://127.0.0.1:8787`, and the PS4's HDMI port is set with `PS4_HDMI` (default 3).
- README for a new user: what you need, what it can't do, install, configuration, API.
- The game line's "Tonight" / "Today" follow the server's time zone (or `TZ`), local teams come from Sports Remote or `LOCAL_TEAMS` (no built-in city), and `HOMEWATCH=0` skips the Fire TV watcher on Android TV. The unused weather endpoint code is gone: Orbit fetches its own weather.

### Fixed

- `GET /glance` and `POST /glance/launch` answer normally (no game) when Sports Remote is down instead of dropping the connection.

### Included from before 1.0.0

- `POST /hdmi/<1-4>`: switch the TV's HDMI input over ADB (wakes the PS4 first).
- `GET /glance` and `POST /glance/launch`: Orbit's game line and weather, and starting a game through Sports Remote.
- Keeps `homewatch.sh` running on the TV: Home goes to Orbit, a lost HDMI picture goes back to the launcher, and Spotify opening itself (Alexa, Spotify Connect) goes to Orbit's idle mode.

[Unreleased]: https://github.com/jwizenfeld04/Orbit-Helper/compare/v1.0.1...HEAD
[1.0.1]: https://github.com/jwizenfeld04/Orbit-Helper/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/jwizenfeld04/Orbit-Helper/releases/tag/v1.0.0
