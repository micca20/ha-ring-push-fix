# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [0.1.1] - 2026-10-25

### Changed
- Published on GitHub for HACS: the manifest documentation and issue_tracker now point at github.com/micca20/ha-ring-push-fix. No code changes.

## [0.1.0] - 2026-10-20

### Added
- Patches `ring_doorbell` `RingEventListener._get_ring_event` at startup: Ring pushes without `ding.id` get a deterministic synthetic id (SHA-1 of device id, created_at and subtype), and a missing `created_at` falls back to `eventito.timestamp`. Any other parse error is logged and the message skipped instead of counting toward firebase_messaging's 3-strike shutdown.
- Skips the patch, and logs that the integration can be removed, once the installed library no longer has the unguarded `event["ding"]["id"]` lookup.
- Receiver watchdog: every 5 min, reloads a Ring config entry whose push receiver has stopped while still expected to run (at most once per 30 min per entry).
