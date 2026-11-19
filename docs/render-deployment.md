# Deploying PowerAble on Render

This project ships with a Render Blueprint (`render.yaml`), a Gunicorn entry
point (`wsgi.py`), and a start script (`scripts/render_start.sh`).

The default Blueprint uses:

- a **free Render web service** running Gunicorn;
- a **free Render Postgres** database;
- automatic Alembic migrations on every start;
- an optional first-admin bootstrap from environment variables;
- HTTPS-only session cookies and Render's proxy headers;
- QR registration links built from `RENDER_EXTERNAL_URL`.

The free tier is suitable for a demo, not for a permanent smart-outlet
installation. Read [Free-tier limits](#free-tier-limits) before you start.

## What the start command does

`scripts/render_start.sh` runs three steps before serving traffic:

```text
flask --app wsgi:app db upgrade      # apply migrations
flask --app wsgi:app bootstrap-admin # only if no active admin exists
gunicorn ... wsgi:app                # serve the Flask app
```

`wsgi.py` always creates the **production** configuration. It does not depend
on `FLASK_ENV` being set correctly.

Render injects its own `GUNICORN_CMD_ARGS`; the start script clears it so the
port and worker settings stay predictable.

## Before you deploy

The project currently has no Git remote. Render deploys from GitHub, GitLab, or
Bitbucket, so push the repository first.

```powershell
git add .
git commit -m "PowerAble with Render deployment"
gh auth login
gh repo create powerable --private --source . --remote origin --push
```

Or create an empty repository in the GitHub/GitLab web UI, then:

```powershell
git remote add origin https://github.com/<you>/powerable.git
git push -u origin master
```

Confirm that `.env`, `instance/`, and real secrets are not committed.

## Deploy with the Blueprint

1. Sign in at <https://dashboard.render.com>.
2. Choose **New → Blueprint**.
3. Connect the repository and branch that contains `render.yaml`.
4. Render reads the Blueprint and asks for the values marked `sync: false`:
   - **DEVICE_API_KEY** — generate a new key
     (`python -c "import secrets; print(secrets.token_urlsafe(32))"`) and paste
     it here. Do not reuse an old key that was previously committed to Git.
   - **ADMIN_USERNAME / ADMIN_PASSWORD** — optional. Set both to create the
     first admin automatically. Leave both blank to skip bootstrap.
5. Click **Apply**. Render creates the web service and the Postgres database,
   then runs the build and start commands.
6. Wait for the deploy to finish and open
   `https://<service-name>.onrender.com/`.

The Blueprint defaults to the `oregon` region. If you are in Asia, change
`region` to `singapore` in `render.yaml` **before** applying; a resource's
region cannot be changed later.

## Create the first admin

### If you set ADMIN_USERNAME and ADMIN_PASSWORD

The start script creates the admin on the first boot and logs only the
username. Sign in at `/auth/login`, then open `/admin/`.

After the first successful deploy, remove `ADMIN_PASSWORD` from the Render
environment and redeploy. The command skips when an active admin already
exists, so it will not reset a password changed in the console.

### If you skipped bootstrap

Free web services have no Shell access. Add `ADMIN_USERNAME` and
`ADMIN_PASSWORD` under **Environment**, choose **Manual Deploy → Deploy latest
commit**, wait for the restart, then remove `ADMIN_PASSWORD` and redeploy.

On a paid plan you can instead use **Shell**:

```bash
flask --app wsgi:app create-admin --username installer
```

## Configure the device API key

Render stores `DEVICE_API_KEY` as a secret environment variable. Open
**Dashboard → powerable → Environment** to view or rotate it.

Every ESP32 request must send:

```http
X-Device-Key: YOUR_DEVICE_API_KEY_HERE
```

Test the deployed heartbeat after registering a device from the website:

```powershell
$body = @{
  device_id       = "PA-NET-12345678"
  network_mode    = "online"
  connection_type = "router"
  ip_address      = "192.168.1.120"
  hardware_status = "CONNECTED"
  relay_state     = "OFF"
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri "https://<service-name>.onrender.com/api/device/heartbeat" `
  -Headers @{ "X-Device-Key" = "YOUR_DEVICE_API_KEY_HERE" } `
  -ContentType "application/json" `
  -Body $body
```

Expected results:

| Condition | Status |
|---|---|
| Valid key, registered device | `200` |
| Missing key | `401` |
| Wrong key | `403` |
| Server has no `DEVICE_API_KEY` | `503` |
| Unknown or unregistered device | `404` |
| Invalid JSON or field value | `400` |

The key is never rendered into HTML, JavaScript, or JSON responses.

## QR registration links

On Render, the Blueprint sets `PUBLIC_BASE_URL` from Render's
`RENDER_EXTERNAL_URL`, so QR codes point at
`https://<service-name>.onrender.com/register?device_id=...`.

If you attach a custom domain, change `PUBLIC_BASE_URL` in `render.yaml` (or
override it in the dashboard) to:

```text
PUBLIC_BASE_URL=https://your-domain.example
```

Otherwise the QR code will keep using the `onrender.com` address.

Phones can scan the QR from any network because the link is public HTTPS. The
ESP32 still needs to reach the server over the internet; it cannot use the old
LAN-only address after deployment.

## Free-tier limits

Render's free tier is useful for verification only:

- **The web service sleeps after 15 minutes without traffic.** The next request
  takes about a minute to wake it. ESP32 heartbeats can time out during that
  window, so the dashboard may show the outlet offline.
- **Free Postgres expires 30 days after creation.** There is a 14-day grace
  period to upgrade before Render deletes it.
- **Free web services have no Shell access and no persistent disk.** Local
  files, including a SQLite database, are lost on every restart or deploy.
- Free services have 750 instance hours per workspace per month and may be
  restarted at any time.

Upgrade the web service and database to paid plans for an always-on
installation.

## Alternative: SQLite on a persistent disk

This keeps the exact local database architecture, but requires a paid web
service because free services cannot attach disks.

1. Upgrade the web service to the **Starter** plan.
2. Add a persistent disk with mount path `/var/data`.
3. Set:
   ```text
   DATABASE_URL=sqlite:////var/data/powerable.sqlite3
   ALLOW_SQLITE_IN_PRODUCTION=1
   ```
4. Remove the `DATABASE_URL` `fromDatabase` entry from `render.yaml` (or manage
   the variable only in the Render dashboard) and delete the unused Postgres
   instance if you no longer need it.
5. Redeploy. The start script runs migrations against the disk database.

The production configuration refuses SQLite unless
`ALLOW_SQLITE_IN_PRODUCTION=1`, which prevents accidentally losing data on an
ephemeral filesystem.

The local `instance/powerable.sqlite3` file is **not** copied automatically.
The deployed instance starts with an empty database; create an admin and
register devices again, or copy the SQLite file onto the disk from a paid
Render Shell.

## Alternative: paid Postgres

For permanent deployments, change the database plan (for example to
`0.5c-1g`) in `render.yaml` or in the Render dashboard. Upgrade a free database
before its 30-day expiry to keep the data. Paid Postgres supports backups and
does not expire.

## Rotating secrets

- **DEVICE_API_KEY** — update it in Render and in the ESP32 firmware together.
  Requests fail with `403` until both match.
- **SECRET_KEY** — changing it signs every browser session out. Existing
  passwords and device keys are unaffected.
- **ADMIN_PASSWORD** — remove it after the first admin exists. The bootstrap
  command never resets an active admin.

## Troubleshooting

| Symptom | Check |
|---|---|
| Build fails | Render logs; Python version in `.python-version`; `pip install -r requirements.txt` |
| Start fails at `db upgrade` | `DATABASE_URL` is set; free Postgres has not expired; migration logs |
| Login redirects in a loop | `SESSION_COOKIE_SECURE=1` requires HTTPS; do not disable it on Render |
| QR code contains a LAN IP | Set `PUBLIC_BASE_URL` or confirm `RENDER_EXTERNAL_URL` exists |
| Heartbeat returns `503` | `DEVICE_API_KEY` is missing from the Render environment |
| Device shows offline | No heartbeat within `DEVICE_HEARTBEAT_TIMEOUT_SECONDS`, or the free service was asleep |
| `502` after idle | Free service is spinning up; wait about a minute |
| Data disappeared | You are using SQLite without a persistent disk, or free Postgres expired |

## Local production smoke test

To exercise the production configuration on Windows without Render:

```powershell
$env:SECRET_KEY = "replace-with-a-64-character-random-value"
$env:DATABASE_URL = "sqlite:///C:/absolute/path/to/instance/prod-smoke.sqlite3"
$env:ALLOW_SQLITE_IN_PRODUCTION = "1"
$env:DEVICE_API_KEY = "YOUR_DEVICE_API_KEY_HERE"
$env:ADMIN_USERNAME = "installer"
$env:ADMIN_PASSWORD = "correct-horse-9"
$env:SESSION_COOKIE_SECURE = "0"   # only because this smoke test uses HTTP

.\.venv\Scripts\python.exe -m flask --app wsgi:app db upgrade
.\.venv\Scripts\python.exe -m flask --app wsgi:app bootstrap-admin
.\.venv\Scripts\python.exe -m flask --app wsgi:app run --port 5001
```

Delete the smoke-test database afterwards. Never set
`SESSION_COOKIE_SECURE=0` on Render.
