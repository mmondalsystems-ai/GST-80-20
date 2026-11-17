# Deployment

## Windows PC: local SQLite database

`requirements.txt` contains SQLite/common dependencies only. PostgreSQL server
installations additionally use `requirements-postgres.txt`, and the installers
request a binary PostgreSQL driver rather than attempting source compilation.
For an offline PostgreSQL deployment, build the bundle with
`./make-offline-bundle.sh --postgres`; the default bundle is for SQLite.

Copy the app to a **non-synced local folder** on the client PC, then run
`deploy.bat`. This BAT is source-only and does not invoke the separate
`distribution/windows/GST-80-20-Setup.exe`. It requires compatible Python
3.11-3.13 (64-bit) and internet or a compatible `wheelhouse/`. It always
deploys local SQLite; PostgreSQL settings in `.env` or `DATABASE_URL` are
rejected before dependency installation so an existing database cannot be
silently replaced. On first use it asks for a local database folder, persists that
choice in `.env`, and creates `finops.db` there. Later launches retain and display
the resolved location. Do not choose a synced folder and do not run
the working copy from OneDrive, Google Drive, or Dropbox: `var/finops.db` would
otherwise be a live database in a synced folder. The launcher warns if it
recognizes such a path. It never loads a database from Google Drive and never
publishes a current/shared copy. Another PC running its own BAT has a **separate
database**, even if both PCs use the same backup folder. No cross-PC
synchronization or reconciliation is provided.

On first launch, the BAT asks for an existing Google Drive **mirrored** folder
for backups. Supply its local Windows folder path, not a browser sharing URL;
leave it blank to use local `var/backups`. After every successful calculation
save, the app makes an integrity-checked SQLite snapshot and a generated Excel
report there. A database backup file is for recovery;
do not run the application against a database inside the synced folder.
If Drive is unavailable, the calculation remains saved locally and the app
shows a backup-failure warning. Check that Drive actually reports *Up to date*
before counting a Drive backup as off-PC protection.

If an earlier release created `gst8020-current.sqlite3` or
`var/handoff-state.json`, this release ignores them; it does not delete them
or replace the local database. Keep them until you have verified which local
database contains the records you need.

## First-use recovery

Before launching, the desktop installer checks local `var/backups` and the
configured backup directory (including a Drive mirror). If calculations already
exist, it retains the current database. Otherwise it lists compatible backups
and asks for a number and explicit `RESTORE` confirmation. Enter skips recovery.
Integrity, schema and foreign-key checks reject invalid backups. Original
accounts/passwords and frozen runs are restored. A pre-restore database is saved in
`var/recovery`. Close all other application processes before restoring. This is
manual recovery, not automatic synchronization. Copy old local backups into
`var/backups` if recovering after a reinstall.

The client handoff files are published in `distribution/` in GitHub. Its ZIP
excludes the distribution folder itself and all issuer/client runtime material.

For a fresh PC, distribute `install_from_zip.bat` separately. It asks for the
repository ZIP, prepares the application under `%LOCALAPPDATA%\GST-80-20`, and
starts the portal. No licence file or activation step is required.

---

The application is a Python ASGI web app. The Windows BAT uses local SQLite; a
separate server deployment may use PostgreSQL. It serves every
asset itself — no CDN, no web fonts, no outbound internet — so it runs on an isolated
server on the client's network.

**For a normal install, use `QUICKSTART.md` — one command does everything below.**
This document is for the parts the installer deliberately leaves to you (HTTPS, backups)
and for anyone who would rather set it up by hand.

---

## 1. What the server needs

| | |
|---|---|
| Python | 3.11 or newer |
| PostgreSQL | 13 or newer — optional; without it the portal runs on a local SQLite file |
| Disk | ~200 MB for the app, plus the database. One month is roughly 1,200 stored rows |
| Network | Inbound HTTP/HTTPS only. No outbound access required |

---

## 2. The one-command install

```bash
sudo ./install.sh          # Linux/macOS
.\install.ps1              # Windows Server, from an elevated PowerShell
```

It checks Python, creates `.venv`, installs the dependencies, provisions the PostgreSQL
role and database, writes `.env` with a generated `SECRET_KEY`, creates the first
administrator with the temporary password, registers a service, and verifies the portal
answers on its port before reporting success. It stops with a plain explanation if any
step cannot complete.

Useful options:

| | |
|---|---|
| `--port 9000` | serve on a different port |
| `--sqlite` | skip PostgreSQL even if it is installed |
| `--no-service` | install without registering a startup service |
| `--uninstall` | remove the service; the database, `var/` and `.env` are left alone |

Running it again upgrades in place. It keeps an existing `.env` untouched — including the
database password — so a re-install never breaks a working configuration.

### Air-gapped servers

`pip` is the only step that wants the internet. On a machine that has it, running the same
operating system and Python version as the server:

```bash
./make-offline-bundle.sh
```

That fills `wheelhouse/` with every dependency. Copy the whole folder to the server and run
the installer there — it finds `wheelhouse/` and installs from it without reaching out.
Python wheels are built per platform, so a bundle made on macOS will not install on Linux.

---

## 3. Setting it up by hand

Skip this if you used the installer.

```sql
CREATE ROLE finops LOGIN PASSWORD 'choose-a-strong-password';
CREATE DATABASE finops OWNER finops;
```

On PostgreSQL 15 and newer, a role no longer gets `CREATE` on the `public` schema
automatically. If you create the database as another user, also run, connected to it:

```sql
GRANT ALL ON SCHEMA public TO finops;
```

Then:

```bash
python -m venv .venv
. .venv/bin/activate           # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`:

```
DATABASE_URL=postgresql+psycopg2://finops:choose-a-strong-password@localhost:5432/finops
SECRET_KEY=<paste the output of the command below>
SESSION_HTTPS_ONLY=1
BOOTSTRAP_ADMIN_EMAIL=chiranjib.sarma@protivitiglobal.in
ORG_NAME=Oswal Group
```

Generate the secret key — it signs session cookies, so everyone is signed out if it
changes, and sessions can be forged if it leaks:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Every setting has a working default, so a missing `.env` is not a failure: the application
generates a secret key into `var/secret.key`, falls back to a SQLite file at
`var/finops.db`, and saves the temporary first administrator password into
`var/first-admin-password.txt`. That file is deleted automatically once the administrator
changes their password.

New installations use `admin@oswalgroup.net` / `admin123456789`. Change this
shared temporary password immediately after login. Existing accounts are not
reset by reinstalling or upgrading. Each saved calculation creates its own
timestamped backup; monitor backup space and apply a client-approved retention
policy.

The tables are created on first start. No migration step is needed for a new install.

---

## 4. Run it

The installer already registers a service — systemd on Linux, a startup task on Windows.
What it deliberately does not do is terminate TLS, because that needs your certificate.
Put a reverse proxy in front, as below, and set `SESSION_HTTPS_ONLY=1` once it is serving
over HTTPS.

### Linux — systemd behind nginx

The installer writes this file for you. It is reproduced here so you can see what it does
and adjust it.

`/etc/systemd/system/finops.service`:

```ini
[Unit]
Description=Finance Operations Portal
After=network.target postgresql.service

[Service]
User=finops
WorkingDirectory=/opt/finops
EnvironmentFile=/opt/finops/.env
ExecStart=/opt/finops/.venv/bin/uvicorn app.main:app \
          --host 127.0.0.1 --port 8080 --workers 4 --proxy-headers
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now finops
```

nginx in front — note the upload limit, because the Tally exports are a few hundred
kilobytes each but grow with history:

```nginx
server {
    listen 443 ssl;
    server_name finops.oswalgroup.local;
    ssl_certificate     /etc/ssl/certs/finops.crt;
    ssl_certificate_key /etc/ssl/private/finops.key;

    client_max_body_size 64M;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;          # a first run on a large month takes a while
    }
}
```

### Windows Server — as a service behind IIS

`uvicorn` runs fine on Windows. Register it with [NSSM](https://nssm.cc/) or
`sc.exe` so it starts with the machine:

```
nssm install FinOpsPortal "C:\finops\.venv\Scripts\uvicorn.exe" ^
      "app.main:app --host 127.0.0.1 --port 8080 --workers 4 --proxy-headers"
nssm set FinOpsPortal AppDirectory C:\finops
nssm start FinOpsPortal
```

Then put IIS in front with **Application Request Routing** reverse-proxying to
`http://127.0.0.1:8080`, and raise the request size limit
(`Request Filtering → Edit Feature Settings → Maximum allowed content length`) to at
least 64 MB.

If IIS is not available, point `uvicorn` at the network interface directly and let
Windows Firewall control who can reach it:

```
nssm set FinOpsPortal AppParameters ^
    "app.main:app --host 0.0.0.0 --port 8080 --workers 4"
```

Run it behind a reverse proxy in production either way, so TLS terminates somewhere
that holds the certificate.

### Serve over HTTPS

Set `SESSION_HTTPS_ONLY=1` once TLS terminates in front of the app. The session cookie
is then only sent over HTTPS. Leave it `0` for a plain-HTTP pilot, or sign-in will not
work.

---

## 5. Verify the install

```bash
curl http://127.0.0.1:8080/healthz     # -> ok
```

Then in a browser: sign in, go to **GST 80:20 → New calculation**, upload a known month,
and check the headline percentage against the last figure the team calculated by hand.

---

## 6. Backups

Calculations, resolutions, the GSTIN master and the audit trail are in the
database. The uploaded spreadsheets are not needed to
reproduce a saved run, because each run stores its own rows and masters.

For the Windows-PC workflow above, `BACKUP_DIR` may point to the client's existing
mirrored Drive folder. A successful calculation creates one dated SQLite
snapshot per PC per local calendar day after its first run. Check that Google
Drive has actually synced; a successful local file copy does not confirm
upload to Google's cloud. Keep historical dated backups and test restoration
on a spare PC.

For a PostgreSQL deployment, the app also attempts `pg_dump` after every
successful calculation save; `pg_dump` must be installed and on PATH. A manual backup:

```bash
pg_dump -Fc -U finops finops > /backups/finops-$(date +%F).dump
```

Restore:

```bash
pg_restore -U finops -d finops --clean /backups/finops-2026-09-01.dump
```

Keep at least one backup per financial year end — the audit trail is the record of who
changed which figure, and it should outlive the assessment period.

---

## 7. Upgrading

Take a backup, replace the application files, and run the installer again:

```bash
sudo ./install.sh
```

It reuses the existing environment, installs any new dependencies, keeps your `.env` and
its database password, and restarts the service. By hand instead:

```bash
sudo systemctl stop finops
. .venv/bin/activate && pip install -r requirements.txt
sudo systemctl start finops
```

New columns are not added automatically by the app beyond the initial table creation. If
a future version changes the schema, that release will ship its migration SQL alongside
these notes.

---

## 8. Adding the next module

The portal reads its home page from `app/catalogue.py`. To add a module:

1. Add its entry to `SOLUTIONS` with `status: "live"` and a `url`.
2. Create `app/routers/<module>.py` with an `APIRouter(prefix="/<module>")`.
3. Mount it in `app/main.py` with `app.include_router(...)`.

Sign-in, roles, the audit helper and the shared creditors master are already available
to it — see `app/routers/gst8020.py` as the worked example.
