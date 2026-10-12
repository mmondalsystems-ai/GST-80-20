# Quick start

Copy this folder to the server, then run one command.

For a Windows PC, choose one of these first-time installers:

- `install_from_github.bat` — copy just this BAT to the PC and double-click it.
  It clones the repository into `%LOCALAPPDATA%\GST-80-20` (GitHub sign-in may
  be required for a private repository), then runs `deploy.bat`. Internet and
  Git are required; it attempts to install Git through `winget` if missing.
- `install_from_download.bat` — download the complete repository ZIP, extract
  it to a non-synced local folder, and double-click this BAT within that folder.
  This route needs no Git on the client PC. The extracted folder must include
  `deploy.bat`, `deploy.ps1`, `app/`, and `requirements.txt`.
- `install_from_zip.bat` — recommended for a controlled client handoff. Give
  this BAT to the client separately; it prompts for the complete repository
  ZIP, prepares a local copy, displays the installation ID, validates the
  separately issued licence JSON, and starts the portal. The first run can be
  stopped after displaying the ID and resumed after the licence is issued.

Both wrappers call `deploy.bat`. It installs the Python
packages, creates a local database, starts the portal and opens the browser.
If port 8080 is occupied, it automatically selects the next available port.
Each PC keeps its own independent SQLite database. You can optionally choose
an existing Google Drive mirrored folder for dated backups; it is not used
to load or synchronize the database. See `DEPLOYMENT.md`.
It attempts to install Python 3.12 through `winget` if Python 3.11+ is not
already available. If corporate policy blocks that, install Python manually.
The first-run administrator password is printed in the console; keep it private
and change it after signing in.
New calculations are read-only until that PC has a signed licence at
`var/license.json`. The 30-day term starts on first valid use, not installation.

**Linux / macOS**

```bash
sudo ./install.sh
```

**Windows Server** — right-click `install.ps1` and choose *Run with PowerShell*, or from an
elevated prompt:

```powershell
.\install.ps1
```

That is the whole install. It checks Python, builds an isolated environment, installs the
dependencies, creates the PostgreSQL database and role, generates the secret key and the first
administrator password, registers the portal to start with the machine, and confirms it is
serving before it finishes. When it is done it prints the address to open and the credentials to
sign in with.

Then:

1. Open the address it printed.
2. Sign in with the email and password shown.
3. Change that password under **Administration → Users** — the banner on the home page says so
   until you do, and doing it deletes the copy the installer left on the server.
4. **GST 80:20 → New calculation**, and upload the month's three Tally exports.
   Download the corresponding input templates from that page first if the
   export format is uncertain.

---

### If something is missing

The installer stops with a plain explanation rather than a stack trace, and tells you what to
install. The two common cases:

**No Python.** Install 3.11 or newer and run it again.

| | |
|---|---|
| Ubuntu / Debian | `sudo apt install python3 python3-venv` |
| RHEL / Rocky | `sudo dnf install python3` |
| Windows | [python.org/downloads/windows](https://www.python.org/downloads/windows/) — tick *Add python.exe to PATH* |

**No PostgreSQL.** The installer says so and uses a local SQLite file instead, so the portal
works immediately. That is fine for a pilot or a single preparer. Install PostgreSQL and re-run
the installer when several people need it at once.

**No internet on the server.** Build the dependency bundle on a machine that has internet and is
running the same operating system and Python version, then copy the whole folder across:

```bash
./make-offline-bundle.sh     # on the connected machine
sudo ./install.sh            # on the server — it finds wheelhouse/ automatically
```

---

### Other things you may want

```bash
sudo ./install.sh --port 9000    # serve on a different port
sudo ./install.sh --sqlite       # skip PostgreSQL even if it is installed
./install.sh --no-service        # install without registering a startup service
sudo ./install.sh --uninstall    # stop and remove the service; data is left alone
sudo ./install.sh                # run it again any time to upgrade in place
```

Re-running is safe. It never touches the database contents, and it keeps your `.env` — including
the database password — exactly as it is.

`DEPLOYMENT.md` covers the rest: HTTPS, backups, and adding the next module.
