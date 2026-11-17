# GST 80:20 Finance Operations Portal

A web application for the Accounts & Finance department. The portal home page is a
catalogue of solutions; the first live module is the
**GST 80:20 Input Credit** calculation. Its calculation engine is a web-safe port of
the client-approved `reference/80-20.py` script with subsequently approved business updates.

## Calculation authority

The attached legacy Python file remains the baseline for filtering, party resolution,
keyword treatment, bank handling, RCM behaviour, approved party corrections, and the
duplicate-CGST repair. Approved later updates add the replacement GST-registration
export, state-aware multiple-GSTIN handling, and GST-ledger evidence of registration.
`tests/test_golden_workbook.py` compares
all 1,232 detail rows and all 20 review items with the approved July workbook.
`tests/test_app_workflow.py` verifies that upload, persistence, and download preserve
those exact results.

Stored keyword edits and manual ledger overrides do not change new calculations.
The version-controlled engine controls line selection, party and GSTIN resolution,
eligibility, GST status and reviews. Project gap/exposure and supplier rankings are clearly labelled
supplementary views computed from those saved rows, never alternative inputs
to the Python calculation.

Everything is served from the application itself — no CDN, no web fonts, no outbound
internet — so it runs on an isolated network.

```
deploy.bat           source-only Windows setup and local launch (Python 3.11-3.13 required)
install_from_github.bat   clone a fresh local copy from GitHub, then run deploy.bat
install_from_download.bat deploy from an already extracted repository ZIP
install_from_zip.bat    select a repository ZIP, prepare and start the portal
demo.bat / demo.sh   run it on a laptop for a demo — SQLite, no setup
demo-inputs/         local-only sample Tally exports; never committed to Git
seed_demo.py         pre-loads the sample month
install.sh           one-command install for Linux and macOS
install.ps1          the same for Windows Server
make-offline-bundle.sh   builds wheelhouse/ for servers with no internet
app/
  main.py            application, sign-in, portal home page
  catalogue.py       the list of solutions shown on the home page
  models.py          database tables
  auth.py            sessions, roles, the audit helper
  engine/
    readers.py       reads the three Tally exports
    gstin.py         GSTIN structure, checksum and PAN cross-check
    golden.py        the authoritative 80:20 calculation
    calc.py          reporting summaries; its public calculate alias is golden.py
    export.py        Excel outputs
  routers/
    gst8020.py       the module
    admin.py         users and the audit trail
  templates/         Jinja2 pages
  static/app.css     the single stylesheet
tests/
  test_rules.py      asserts a single Python-aligned calculator and key rules
  test_golden_workbook.py   compares every July output row and review item
  test_app_workflow.py     exercises upload, export and freeze through HTTP
  test_august_synthetic.py checks month handling (not genuine August data)
```

**Demoing it on a laptop? See `DEMO.md`** — double-click `demo.bat` on Windows, or run
`./demo.sh` on macOS/Linux. No database, no administrator rights.

**Starting on a fresh Windows PC?** Double-click `deploy.bat`. If Python 3.11+
is absent, it attempts a per-user Python 3.12 install through Windows Package
Manager (`winget`); if that is unavailable or restricted, install Python manually
and select *Add Python to PATH*. It uses `install.ps1` to create `.venv`,
install `requirements.txt` (from the internet or an optional local `wheelhouse/`),
ask once for the local SQLite database folder, create the database there, and generate the initial administrator password.
On first launch it also asks for an existing **mirrored** client-only
Google Drive folder for dated database and Excel backups. The live SQLite database remains local
and is never loaded from or published as a shared Drive copy. A dated,
integrity-checked, uniquely timestamped database backup and a generated Excel report are made after every successful calculation save.
It then starts the portal at `http://127.0.0.1:8080` and opens a browser. Keep the
console open; Ctrl+C stops the server. Use `deploy.bat 8081` for a different port.
If the requested port is occupied, the launcher automatically uses the next
available port and opens the browser at that address.
This launcher binds only to the local PC. Each installation has an independent
database; the app does not synchronize users' records across PCs.
See `DEPLOYMENT.md` for backup and recovery steps.

Three first-run wrappers are provided. Give `install_from_github.bat` to someone
with access to the GitHub repository and internet. It installs Git if needed,
prompts for GitHub sign-in when the repository is private, clones into the
PC's `%LOCALAPPDATA%\GST-80-20` folder, and starts `deploy.bat`. Alternatively,
download the complete repository ZIP, extract it to a non-synced local folder,
and run `install_from_download.bat` inside that folder. Both accept an optional
starting port, for example `install_from_download.bat 8081`. Neither wrapper
updates or overwrites an existing database. The GitHub wrapper reuses an
existing installation rather than pulling new code; upgrades need a separate,
backed-up process.

For a controlled client handoff, distribute `install_from_zip.bat` separately
from the repository archive. It installs a selected repository ZIP into the
local `%LOCALAPPDATA%\GST-80-20` folder, prepares the PC and starts the portal.
No licence file or activation step is required.

New installations start with `admin@oswalgroup.net` / `admin123456789`.
This is a shared temporary password: change it immediately after signing in.
Upgrading does not reset an existing user's password. Each calculation save
creates a separate database backup; monitor disk/Drive space and archive old
snapshots according to your retention policy.

Client handoff files are in `distribution/`. On first use, the launcher checks
both local and configured backup folders and offers explicit, validated recovery
only if the current database has no calculations. Restored accounts retain
their original passwords. See `DEPLOYMENT.md` for recovery safeguards.

Only source code and the original `reference/80-20.py` are intended for GitHub.
Client Excel exports and the approved July output are ignored. The workbook
parity and HTTP tests require those private files to be placed back under
`reference/` locally; `seed_demo.py` likewise requires private `demo-inputs/`.

To install it on a server, one command — see **QUICKSTART.md**:

```bash
sudo ./install.sh          # Linux/macOS
.\install.ps1              # Windows Server
```

**DEPLOYMENT.md** covers HTTPS, backups, air-gapped servers and adding the next module.

---

## What the GST 80:20 module does

Under Notification 03/2019-CT(Rate), a promoter must buy at least 80% of inputs and
input services from registered suppliers, tested **per project for the financial year**,
with tax payable on any shortfall. This module measures that position and shows what is
still movable.

### Every month

1. **New calculation** — download the three input templates on that page, then
   upload matching Day Book, Search Voucher and Creditors exports. The first
   worksheet contains the exact expected Tally headers; the second explains
   the fields. Uploading a blank template is rejected.
2. The module resolves the party behind every cost line, applies the rules, and stores
   the result as a permanent snapshot.
3. Work the **review queue** for items identified by the golden script.
4. A user with edit access **freezes the month** after the calculation has been approved.

Freezing locks every row and review decision: a frozen month cannot be modified or
replaced by a new upload. Reporting and downloads remain available, and the freeze
actor and timestamp are permanently audited. Only an Administrator can remove the
entire frozen snapshot; that exceptional action requires a reason, creates a safety
backup, and is audited before the month can be run again.
Existing frozen snapshots are not rewritten when the calculation engine changes;
their exported "Masters Used" tab shows the settings originally saved with them.

Re-running a draft month does not overwrite anything: use a unique label, the
earlier run is kept and marked superseded, and decisions already made are carried
forward to the new run. A blank label receives a unique timestamp automatically.

### The rules

| | |
|---|---|
| Voucher key | `Date \| Voucher_Type \| Voucher_No` — the number alone repeats across types |
| Party | resolves from the Approved Search Voucher, prioritising a matching GST-bearing creditor, then the largest credit-only non-tax line |
| Reverse charge | evaluated for the **whole voucher**. If any Day Book entry is exactly `Output CGST on RCM`, `Output SGST on RCM` or `Output IGST on RCM` after case/space normalisation, the concerned party is Eligible and Registered even with a blank GSTIN. |
| Replacement creditor master | accepts `Fixed_Group`, `Reg_No`, `PAN_No`, state and company fields; non-creditor groups and generic accounting ledgers are excluded. Multiple registrations are selected from state/tax evidence, never row order. |
| Eligibility | Keywords match `Account_Name` only, plus the two approved always-ineligible ledgers, unless the whole-voucher Output-RCM override applies. |
| GST status | Populated GST No. → Registered. A genuine CGST/SGST/IGST ledger also makes the concerned party Registered even if the master is blank; no GSTIN is invented. |
| Head | Every included transaction is reported as `Expenses`, matching the approved workbook |

The approved script's specific party/GST corrections and bank behaviour are retained
in `app/engine/golden.py`. Changing them requires a newly approved golden fixture and
a corresponding regression-test update.

### What each page is for

| Page | |
|---|---|
| Overview / Year to date | The financial-year position per project. A single month is an indicator; this is the test |
| Selected period | Inclusive start/end-month consolidation within one financial year, frozen-only by default, with a complete row-level export |
| Run detail | One month, with the summary pivot and where to look next |
| 80-20 table | Every reportable line, filterable, exactly as the original spreadsheet |
| Rectification queue | Assign, decide and record. A GSTIN entered here updates the follow-up master; it must also be present in the next uploaded Creditors export to affect a calculation. |
| Unregistered suppliers | Ranked by spend, spelling variants grouped, so it is clear which few relationships would move the percentage |
| Exceptions | Credit-side reallocation JVs, invalid GSTINs, parties that resolved to a round-off or bank ledger |
| Creditors & GSTIN | The master, plus a fill-in sheet to hand out and import back |
| Archived ledger corrections | Historical settings remain visible for audit but can no longer change new calculations. Correct the source exports or approve a revised Python file. |
| Masters | The Python keyword list is locked. Fixed/Parent Group selections, allowed creditor groups and company GST state are versioned with each run; dashboard-only thresholds are separate. |
| Audit trail | Who changed what, permanently |
| AI Workspace | Optional Anthropic explanations, risk summaries, rectification grouping, result Q&A, creditor proposals, narratives and usage FinOps. Disabled by default and isolated from the calculation engine. |

### Optional Anthropic AI Workspace

The core portal does not require an API key or internet. To make AI available on
an installed machine, add `ANTHROPIC_API_KEY=...` to that installation's `.env`,
restart the portal, then open **AI Workspace** as an Administrator. Turn on the
master switch and only the individual capabilities the client has approved.

No data is sent merely by enabling a checkbox or opening the page. A bounded,
read-only result snapshot is sent only when a signed-in user presses an AI action.
The approved Python result remains authoritative: the integration cannot execute
SQL, change a classification, update a frozen month or automatically write an AI
suggestion into the creditor master. Suggestions have an explicit human
approve/reject record and verified changes are made separately on Creditors & GSTIN.

The same page contains the FinOps view: requests, input/output/cache tokens,
latency, errors, request IDs and estimated USD cost. Because model prices change,
an Administrator enters the current per-million input and output rates. A monthly
cost guardrail can stop further requests; zero means no application-level cap.
The Anthropic Console's own workspace/spend limits should remain the primary
billing control.

An Administrator can also paste or rotate the Anthropic key directly in AI
Workspace. The field is write-only: after saving, the UI shows only an SHA-256
fingerprint. The encrypted value is stored in the application database and the
encryption key is derived from `SECRET_KEY`. Keep `SECRET_KEY` stable and secret;
changing it makes previously stored integration keys unreadable. Key replacement
and removal are audited without recording the secret value.

### Render deployment

The repository includes `render.yaml` for a Render Blueprint. It provisions the
FastAPI web service, Render Postgres, a persistent file disk, HTTPS-only session
cookies and a database-aware `/readyz` health check. In Render, create a Blueprint
from this repository and provide `BOOTSTRAP_ADMIN_PASSWORD` when prompted.

Postgres holds application records and the encrypted UI-managed Anthropic key.
The persistent disk holds the installation identity, archived
uploads, Excel reports and database dumps across redeployments. Do not remove the
disk or change its mount path after deployment. Keep one web instance because a
service with an attached Render disk cannot be horizontally scaled; Postgres still
makes concurrent browser use safe within that instance.

See [RENDER_DEPLOYMENT.md](RENDER_DEPLOYMENT.md) before applying the Blueprint to
an existing service. In particular, moving an existing SQLite deployment to
Postgres requires an explicit data migration; it is not performed automatically.

---

## Roles

| Role | |
|---|---|
| **Administrator** | everything, plus users and masters |
| **Preparer** | uploads files, runs calculations, resolves rectifications, edits the GSTIN master |
| **Viewer** | reads results and reports; cannot change anything |

Every change is written to the audit trail with the person's name — which is what
explains a movement in the registered percentage to an auditor.

---

## Running the tests

On a development machine, install `requirements-dev.txt` into the virtual
environment first. The workbook tests also need the private July files under
`reference/`; those files are intentionally not in GitHub.

```bash
python -m pip install -r requirements-dev.txt
python -m tests.test_rules             # single-engine and Python-rule checks
python -m tests.test_golden_workbook   # exact 1,232-row/20-review comparison
python -m tests.test_app_workflow      # upload, export, audit and permanent freeze
python -m tests.test_august_synthetic  # date-shift regression, not real August
```

---

## A note on the exposure figure

The shortfall amount and the tax on it are **indicators**, built from rates that are
editable on the Masters page. Confirm the rates and the computation basis with your GST
advisor before using the figure in a filing. Review flags do not change the golden
Registered/Unregistered classification; they identify records for follow-up.
The app additionally shows the source Daybook narration and flags structurally
invalid GSTINs on Data Quality views. The email no longer supplies competing
classification rules.
The four approved workbook sheets remain unchanged; the source script's
Narration column is intentionally blank in the canonical 80-20 Table export.
