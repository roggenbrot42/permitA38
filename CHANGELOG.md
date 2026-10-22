# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- Docker support: multi-stage `Dockerfile` (non-root, health check, pinned dependencies), `docker-compose.yml`
  (hardened), HTTPS reverse-proxy examples (Caddy, nginx) and `docs/docker.md`.
- Environment-variable configuration for server hosting (`PERMITA38_*`: host, port, secret key / key file, CORS,
  token lifetime, proxy trust, cookie `Secure` flag, log level) and container mode that keeps the generated secret in
  the data volume instead of a per-device YAML file.
- First-run bootstrap of the boss account from `PERMITA38_BOOTSTRAP_*` (closes the public setup page) and the
  `permita38 create-user` command.
- The auth cookie gets the `Secure` flag on https / behind a trusted proxy; logging goes to stdout.
- GitHub Actions workflow building the image on pull requests and publishing multi-arch images to ghcr.io on tags.

### Changed
- Precedence of `PERMITA38_DATA` is now command line > environment > YAML (it used to be below the YAML file), and
  environment values are no longer written into the YAML file.

## [0.2.0]

### Changed
- Renamed the project from *Timely* to **permitA38** (package, CLI and config are `permita38`).
- New architecture inspired by stustapay: a FastAPI backend (routers → services → storage) and a separate
  vanilla-JS frontend in `web/`, connected by an OpenAPI contract (`api/openapi.json`, interactive docs at `/api/docs`).
- Per-device YAML configuration (`~/.config/permita38/config.yaml`) replaces the old remembered-path file.
- Data folder defaults to `~/permitA38`; environment variable is `PERMITA38_DATA`.
- Endpoints are RESTful (`POST` create, `PUT` update, `DELETE` returns 204) and errors are `{"detail": ...}`.

### Added
- Login screen with username and password, signed bearer tokens, login throttling and password change.
- `permita38 set-password` recovery command and `permita38 show-openapi`.
- Optional separate hosting of the frontend (CORS via `cors_origins`, `serve_web: false`).
- pytest suite (TestClient), ruff/mypy tooling, pre-commit and GitHub Actions CI.
- GPL-3.0-or-later license.

### Removed
- PIN-based user picker (replaced by real accounts).

## [0.1.0]
- Initial version as *Timely*: time tracking, weekly goal, forecast, offers, invoices, expenses, tax estimates.

[Unreleased]: https://github.com/roggenbrot42/permitA38/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/roggenbrot42/permitA38/releases/tag/v0.2.0
