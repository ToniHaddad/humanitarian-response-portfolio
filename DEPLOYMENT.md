# Server mode: deployment

Server mode runs the whole portfolio continuously on one always-on Linux machine with Docker Compose. Local demo mode (`python run.py`) is unchanged and needs none of this.

> **Built and run on a GitHub Actions Linux runner; not yet run on a long-lived server.** The workflow `.github/workflows/server-mode.yml` builds the image, runs `docker compose up -d`, waits for all four services to report healthy, runs `scripts/server_smoke.py` and tears the stack down. The smoke script checks: the homepage and ports 8501-8520 answer `/_stcore/health`; the API `/health` is healthy and `/docs`, `/reference` and `/openapi.json` load; a write without credentials (and with a wrong login) is refused, a write with the staff login or the API token succeeds and appears in the audit log; the scheduler heartbeat is recent and the position simulator and alert evaluation have a successful run after the stack started; the newest simulated position is newer than the start; the backup service wrote a backup file and `scripts/restore_db.py` restores it into a scratch path; no port is published on all interfaces; the API is healthy again after `docker compose restart api`. You can run the same script on a server: `python3 scripts/server_smoke.py`. See "Not verified / limitations" for what that does not prove.

## What runs

One image (`Dockerfile`), four services (`docker-compose.yml`), one named volume for the database shared by all of them (WAL mode is on, so readers and one writer coexist).

| Service | What it does | Port (on `SERVER_BIND_ADDRESS`) | Health check |
|---|---|---|---|
| `apps` | The homepage and every built project (`python run.py --no-browser`) | 8500 (homepage), 8501-8520 (projects 01-20) | each Streamlit `/_stcore/health` answers |
| `api` | The FastAPI operations API (`python -m shared.api`) | 8600 (`/docs` for the interactive page) | `GET /health` answers 200 |
| `scheduler` | APScheduler: data pipeline, alert rules, position simulator, database backup job | none | its heartbeat in the database is under 5 minutes old |
| `backup` | `scripts/backup_db.py` every `BACKUP_INTERVAL_SECONDS` | none | a backup newer than two intervals exists |

Every service has `restart: unless-stopped`, so they come back after a crash or a reboot of the machine (Docker must start at boot: `sudo systemctl enable docker`).

The position simulator is not a separate container: it is the scheduler's `position_simulator` job (every 60 seconds by default, switched on or off from the Project 20 app). A second process would write every position twice.

All services use the same `TZ`, because stored timestamps are naive local time. Change `TZ` in `.env` before the first start and do not change it afterwards on a database that has data.

## Prerequisites

- A Linux machine with Docker Engine and the Docker Compose plugin (`docker compose version`).
- A private network between the staff and this machine, such as a mesh VPN, and the address the machine has on it.
- About 2 GB of free disk and 2 GB of RAM for the apps container (the apps are many Streamlit processes).

## First start

```bash
git clone <your private copy of this repository> portfolio && cd portfolio
cp .env.example .env
nano .env            # see below
docker compose up -d --build
docker compose ps    # wait until every service says "healthy" (the first start takes a few minutes)
```

In `.env` set:

- `SERVER_BIND_ADDRESS`: the machine's address on the private network. The default `127.0.0.1` makes everything reachable only from the machine itself.
- `TZ`: the time zone staff work in, for example `Europe/Paris` style names.
- `PORTFOLIO_STAFF_USERNAME`, `PORTFOLIO_STAFF_PASSWORD`, `PORTFOLIO_API_TOKEN`: real values. The value `change-me` is refused by the login, so the server never runs on the example values.
- `BACKUP_INTERVAL_SECONDS` and `BACKUP_KEEP`: how often to back up and how many copies to keep.

`.env` is excluded from git (`.gitignore`) and from the image (`.dockerignore`). Hostnames, addresses and secrets live only there. On first start the entrypoint builds the demo database on the volume if it is empty (under a lock, so only one service does it).

## Reaching it over a private network

1. Install the mesh VPN on the server and on each staff device, and join them to the same private network.
2. Put the server's private-network address in `SERVER_BIND_ADDRESS` and run `docker compose up -d` again. Docker then publishes the ports only on that address.
3. Staff open `http://<private address or name>:8500` (homepage), `:8501` to `:8520` (projects) and `:8600/docs` (API).
4. Do not forward these ports on a router, do not open them in a public firewall, and do not put them behind a public reverse proxy. If the machine has a public address, check from outside the private network that the ports are closed.

Docker publishes ports by editing the firewall itself and can bypass simple host firewall rules; the safest rule is the one in `.env`: never bind to an address you do not want reachable.

## Staff login

- **API:** in server mode every write (`POST`/`PUT`/`PATCH`) needs `Authorization: Bearer <PORTFOLIO_API_TOKEN>`. Reads do not. The API implements this in `shared/api.py` using `shared/auth.py`.
- **Apps:** in server mode (`PORTFOLIO_MODE=server`) the shared page header calls `shared.auth.require_staff_login()` (username and password from `.env`) on the homepage and on every app that can change data. The four read-only pages (03 classifier lab, 09 needs map, 17 operations room board, 19 sitrep search) stay open, so the board keeps updating with nobody logged in.

## Scheduled jobs

The scheduler runs these with nobody logged in (intervals are rows in `scheduled_jobs`, shown and editable in the Project 20 app):

| Job | Default interval | What it does |
|---|---|---|
| `pipeline_ingest` | 1 hour | the field data pipeline (Project 07) |
| `alert_evaluation` | 5 minutes | evaluates the alert rules, which keeps the operations room board current |
| `position_simulator` | 60 seconds | writes simulated vehicle positions |
| `db_backup` | 24 hours | a copy into the backup volume, keeping the last 5 |

Each run is recorded in `job_runs`; `GET /health` on the API and the Project 20 page show the last success and an overdue flag for each job.

## Backups

The `backup` service writes a verified copy with SQLite's online backup API every `BACKUP_INTERVAL_SECONDS` (default 6 hours) into the `portfolio-backups` volume as `portfolio-YYYYMMDD-HHMMSS.db`, and keeps the newest `BACKUP_KEEP` (default 14). The scheduler's own `db_backup` job (daily) writes into the same folder with the same names but keeps only 5, so each of its runs prunes the folder down to 5 copies and overrides `BACKUP_KEEP`. To get the larger history, switch the `db_backup` job off in the Project 20 app (scheduled jobs), or accept 5 copies.

Take one by hand:

```bash
docker compose exec backup python scripts/backup_db.py
docker compose exec backup ls -l /backups
```

Backups sit on the same machine. For a real disaster copy them elsewhere on a schedule you control, for example:

```bash
docker run --rm -v <project>_portfolio-backups:/b -v "$PWD":/out alpine tar czf /out/backups.tgz -C /b .
```

(The volume name is the Compose project name plus `_portfolio-backups`; `docker volume ls` shows it.)

## Restore

1. Stop everything that uses the database:
   ```bash
   docker compose stop apps api scheduler backup
   ```
2. See what is available:
   ```bash
   docker compose run --rm --no-deps backup ls -l /backups
   ```
3. Restore the newest backup, or a named one. Without `--yes` the script only says what it would do:
   ```bash
   docker compose run --rm --no-deps backup python scripts/restore_db.py --latest --yes
   docker compose run --rm --no-deps backup python scripts/restore_db.py --backup /backups/portfolio-20261116-090000.db --yes
   ```
   The backup is checked first (`PRAGMA integrity_check`). The database it replaces is kept beside it as `portfolio.db.before-restore-<time>`, and stale `-wal` and `-shm` files are removed.
4. Start again and check health:
   ```bash
   docker compose up -d
   docker compose ps
   ```

Without Docker the same scripts work directly: `python scripts/restore_db.py --target data/portfolio.db --dest data/backups --latest --yes`.

## Updating

```bash
git pull
docker compose up -d --build
docker compose ps
```

The database volume is kept. Take a backup first (`docker compose exec backup python scripts/backup_db.py`). To go back to the demo data (this erases changes): stop the services, then `docker compose run --rm --no-deps apps python -m shared.seed --reset`.

## Logs and troubleshooting

- `docker compose logs -f scheduler` (or `api`, `apps`, `backup`).
- A service stuck "unhealthy": the scheduler heartbeat is written every 30 seconds; if only `scheduler` is unhealthy, check that the database exists and the scheduler log shows jobs.
- Staff cannot log in: the login refuses empty values and `change-me`; check `.env` and recreate the containers (`docker compose up -d`).

## Not verified / limitations

- **Run only on a GitHub Actions runner** (`ubuntu-latest`, loopback only, a few minutes, an empty database volume, generated test credentials). It has **not** been run on a long-lived server, not reached over a private network (mesh VPN) from another machine, not left running for days, and not rebooted: that `restart: unless-stopped` brings the stack back after a machine reboot, that the volumes survive a host restart, and that a backup survives a real restore into a live stack are unconfirmed. The restore check restores into a scratch path inside the container, not over the live database.
- What the smoke run does not cover: logging in to a Streamlit app in a browser (only the health endpoints of the apps are checked), the apps under real use, load, the health checks' behaviour after hours of uptime, backups on the schedule (one backup is written at start), and the scheduler's data-pipeline job in the container.
- Also checked statically by `tests/test_server_mode.py`: `docker-compose.yml` parses; every service has `restart: unless-stopped` and a health check; every service mounts the database volume and uses the same `TZ`; every published port is bound to `SERVER_BIND_ADDRESS` (default loopback), none to all interfaces; `.env.example` holds only placeholders; `.env` is in `.gitignore`. The backup and restore scripts and the health check functions were run on a temporary database and the row counts compared after restoring.
- The `apps` service starts with `--no-services`, so the API and the scheduler run only in their own services. The scheduler probes the homepage on host `apps` and the API on host `api` (`PORTFOLIO_PROBE_HOST`, `PORTFOLIO_PROBE_API_HOST`).
- The `apps` container starts about twenty Streamlit processes; on the runner it was healthy about 40 seconds after start. A slower server may need a larger `start_period`.
- **The app login is one shared staff username and password, not one account per person.** The role is still chosen in the sidebar after login. The login gate is covered by tests (`tests/test_foundation_server_login.py`) but has not been tried on a running server.
- The API's token is one shared secret, not per-user logins.
- The placeholder check in `shared/auth.py` refuses only the lower-case `change-me`; `.env.example` uses exactly that value for that reason.
- The `apps` health check needs every built app to answer; one slow app makes the whole service report unhealthy.
- Backups are on the same machine as the database until you copy them away.
