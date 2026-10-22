# Running permitA38 in Docker

A hosted instance is the alternative to syncing a data folder with Syncthing: you run **one** permitA38 server
(a Raspberry Pi, a NAS, a VPS) and every device – desktop, laptop, phone – simply uses it **in the browser**. There
is nothing to sync, and the same login works everywhere.

> You can still use the desktop launchers (`start-permita38.sh/.bat`) with a synced folder. The two modes are
> independent; don't point a desktop install and a container at the same folder at the same time.

## Quick start (docker compose)

```bash
git clone https://github.com/roggenbrot42/permitA38.git && cd permitA38
cp .env.example .env            # edit: choose your username and a long random password
docker compose up -d --build
```

Open <http://localhost:8765> and sign in with the account from `PERMITA38_BOOTSTRAP_*`. The container creates that
**boss** account on its first start, so the public "create your account" page is never exposed.

Check it:

```bash
docker compose ps                # STATUS should become "healthy"
docker compose logs -f permita38
```

Prebuilt multi-arch images (amd64 + arm64) are published for every release tag:
`ghcr.io/roggenbrot42/permita38:<version>` (and `:latest`). To use one without cloning the repository, copy
`docker-compose.yml` and `.env.example` and delete the `build: .` line.

## What the image contains

| | |
|---|---|
| Base | `python:3.12-slim`, multi-stage build, dependencies pinned in `docker/constraints.txt` |
| User | unprivileged `permita` (uid/gid **10001**), no shell, no home |
| Port | `8765` (HTTP) |
| Volume | `/data` – the data folder (everything permitA38 stores) **and** the generated secret key |
| Health | `HEALTHCHECK` calls `GET /api/health` |
| Logs | stdout (`docker logs`), level via `PERMITA38_LOG_LEVEL` |
| Shutdown | `SIGTERM` → graceful (requests finish, then exit). The exit code is 143 (=128+15) after `docker stop`; that is expected. |

The compose file additionally runs the container with a **read-only root filesystem**, `/tmp` as tmpfs, **all Linux
capabilities dropped**, `no-new-privileges`, and an init process.

## Configuration (environment variables)

Everything can be set through the environment (or `.env`). Precedence: command-line option > environment > YAML
file > default. In a container no YAML file is needed or written.

| Variable | Default | Meaning |
|---|---|---|
| `PERMITA38_BOOTSTRAP_USERNAME` | – | Boss account created at first start (only if there are no users yet; never changes existing users). |
| `PERMITA38_BOOTSTRAP_PASSWORD` / `…_PASSWORD_FILE` | – | Its password (min. 8 characters; prefer the `_FILE` form with a Docker secret). |
| `PERMITA38_BOOTSTRAP_NAME` | = username | Display name of the boss. |
| `PERMITA38_BOOTSTRAP_COMPANY` | – | Business name for the initial settings. |
| `PERMITA38_SECRET_KEY` / `…_SECRET_KEY_FILE` | generated | Key used to sign login tokens (min. 16 chars). If unset, a random key is generated once and kept in `/data/.secret_key` (mode 600), so logins survive restarts. Set it explicitly if you run several replicas. |
| `PERMITA38_DATA` | `/data` | Data folder inside the container. |
| `PERMITA38_HOST` / `PERMITA38_PORT` | `0.0.0.0` / `8765` | Listen address. Changing the port also changes the health check. |
| `PERMITA38_TOKEN_TTL_HOURS` | `12` | Lifetime of a login. |
| `PERMITA38_TRUST_PROXY` | `false` | Honour `X-Forwarded-*` from a reverse proxy. **Only enable if the app can be reached only through that proxy.** |
| `PERMITA38_FORWARDED_ALLOW_IPS` | `*` | Which proxy addresses are trusted when `TRUST_PROXY` is on. |
| `PERMITA38_COOKIE_SECURE` | auto | `true`/`false` forces the `Secure` flag on the login cookie. Automatic = on for https requests (or `X-Forwarded-Proto: https` with `TRUST_PROXY`). |
| `PERMITA38_CORS_ORIGINS` | – | Comma-separated origins allowed to call the API (only needed when the frontend is hosted elsewhere). |
| `PERMITA38_SERVE_WEB` | `true` | Serve the bundled web UI. `false` = API only. |
| `PERMITA38_WEB_DIR` | `/app/web` | Location of the web UI inside the image. |
| `PERMITA38_LOG_LEVEL` | `info` | `debug`, `info`, `warning`, `error`. |
| `PERMITA38_IN_CONTAINER` | `1` (set by the image) | Container mode: no YAML config is written; the secret key lives in the data volume. |

## Users and passwords in a container

```bash
# add a member (prompts for the password; add -it)
docker compose run --rm -it permita38 create-user --username mia --name "Mia Member"

# add another boss, password from a file/secret
docker compose run --rm -v ./secrets/pw.txt:/run/secrets/pw:ro permita38 \
    create-user --username second --name "Second Boss" --role boss --password-file /run/secrets/pw

# reset a forgotten password
docker compose run --rm -it permita38 set-password mia
```

The boss can also add and manage people in the web UI (**Team**).

## Data, backups and updates

All data lives in the `permita38-data` volume, mounted at `/data`.

```bash
# backup: tar the volume
docker run --rm -v permita38-data:/data:ro -v "$PWD":/backup alpine \
    tar czf /backup/permita38-$(date +%F).tar.gz -C /data .

# restore into an (empty) volume
docker run --rm -v permita38-data:/data -v "$PWD":/backup alpine \
    sh -c 'cd /data && tar xzf /backup/permita38-YYYY-MM-DD.tar.gz && chown -R 10001:10001 /data'

# update
git pull && docker compose up -d --build        # or: docker compose pull && docker compose up -d
```

Stop the container (or take the backup from a quiet moment) for a perfectly consistent copy, and keep at least one
backup somewhere that is not on the same machine. If you prefer a **bind mount** (`./data:/data`), make the folder
writable for uid 10001: `sudo chown -R 10001:10001 ./data`.

## HTTPS with a reverse proxy

Never expose the plain HTTP port to the internet: passwords and tokens would travel unencrypted.

### Caddy (automatic certificates) – included

```bash
# .env: PERMITA38_DOMAIN=permita.example.com   (a DNS name that points to this host; ports 80 and 443 reachable)
docker compose -f docker-compose.yml -f docker-compose.proxy.yml up -d --build
```

`docker-compose.proxy.yml` removes the published app port (only Caddy can reach the app), turns on
`PERMITA38_TRUST_PROXY` and `PERMITA38_COOKIE_SECURE`, and starts [Caddy](https://caddyserver.com/) with
`docker/Caddyfile`, which fetches and renews a Let's Encrypt certificate and adds HSTS.

### nginx

See `docker/nginx.conf.example`. Publish the app on localhost only (`127.0.0.1:8765:8765`) and set
`PERMITA38_TRUST_PROXY=true` and `PERMITA38_COOKIE_SECURE=true`.

### Notes

- The app must be served from the **root of a host name** (`https://permita.example.com/`). Serving it under a
  sub-path (`https://example.com/permita/`) is **not supported**.
- Behind Traefik, Nginx Proxy Manager etc. the same two variables apply.

## Security checklist

- [ ] HTTPS in front (see above); `PERMITA38_COOKIE_SECURE=true` (or automatic detection works for you).
- [ ] Use the **bootstrap** variables so nobody can claim the instance through the setup page. Without them, the
      container logs a warning at startup while no user exists.
- [ ] A long, unique password for the boss; passwords via Docker secrets (`…_PASSWORD_FILE`) rather than `.env` if
      others can read the file. Keep `.env` out of git (it is git-ignored) and `chmod 600 .env`.
- [ ] `PERMITA38_TRUST_PROXY=true` **only** when the container is unreachable except via the proxy.
- [ ] Rate limiting: failed logins are throttled per username (5 failures → 5 minute pause). For internet exposure
      add limits at the proxy as well (e.g. Caddy `rate_limit` plugin, nginx `limit_req`) or use a VPN.
- [ ] Back up the volume regularly and test a restore.
- [ ] Keep the image up to date; pin a base digest if you need bit-for-bit reproducibility:
      `docker build --build-arg PYTHON_IMAGE=python:3.12-slim@sha256:<digest> .`
- [ ] Remember that the person with access to the Docker host and the `/data` volume can read all business data.

## Troubleshooting

- **`permission denied` writing to `/data`** – bind-mounted folder not owned by uid 10001 (`chown -R 10001:10001`).
- **Everyone is logged out after a restart** – the secret changed: don't delete `/data/.secret_key`, or set
  `PERMITA38_SECRET_KEY` explicitly.
- **`unhealthy`** – `docker compose logs permita38`; usually a config error (e.g. a too short secret, an invalid
  `PERMITA38_PORT`), which is printed on startup.
- **Setup page appears on the internet** – no bootstrap account was configured; set `PERMITA38_BOOTSTRAP_*` or create
  the boss immediately via `docker compose run --rm -it permita38 create-user --role boss …`.
