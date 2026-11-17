# Running the demo

Everything needed is in this folder. No database to install, no administrator
rights, nothing added to the machine outside this folder.

No licence or activation step is required. Do not use `-Reset` on a demo
installation whose saved work must be retained.

---

## Start it

**Windows** — double-click **`demo.bat`**

**macOS / Linux** — in Terminal:

```bash
cd path/to/finops
./demo.sh
```

The first run takes about a minute while it sets itself up. After that it starts
in a few seconds. Your browser opens automatically at **http://127.0.0.1:8080**.

**Sign in:** `admin@oswalgroup.net` · `demo1234`

Leave the window open — that window *is* the server. Press **Ctrl+C** in it to stop.

---

## Two ways to run the demo

### A. Show the upload — the fuller story

Start it normally (`demo.bat` or `./demo.sh`). The portal opens empty.

1. **GST 80:20 Input Credit** on the home page
2. **New calculation**
3. Attach the three files from the **`demo-inputs`** folder:
   - `DayBookRegister.xlsx`
   - `Search Voucher.xlsx`
   - `Creditors Details.xlsx`
4. **Run the calculation** — it takes a few seconds and lands straight on the result

### B. Start with figures already on screen — safer if time is short

```
    demo.bat -Seed          (Windows)
./demo.sh --seed        (macOS / Linux)
```

July 2026 is already calculated when the portal opens, so you begin on a
populated dashboard. The three files are still in `demo-inputs` if you want to
show the upload afterwards.

---

## A ten-minute walkthrough

| | Where | What to say |
|---|---|---|
| 1 | Home page | Five solutions for Accounts & Finance. GST 80:20 is live; the others are the roadmap on the same portal. |
| 2 | GST 80:20 → Overview | The July aggregate registered share is **81.20%**, but **one project is below 80%**. The headline is context; the project table owns the shortfall test. |
| 3 | Same page, By project | Orchard Amritaya is at **79.04%** with a provisional ₹2,52,171.68 gap (₹45,390.90 at the configurable 18% rate). Rajarhat and Titagarh are above 80%; their surpluses do not erase Orchard's gap. |
| 4 | Review queue | The golden workbook flags **20 review items**. Decisions and GSTIN follow-up are audited annotations; the saved golden classifications do not change until corrected source files are uploaded in a new draft run. |
| 5 | Unregistered suppliers | Filter the list by a project before discussing procurement actions. A cross-project supplier ranking cannot itself close a particular project's gap. |
| 6 | Exceptions | Inspect credit-side entries and **3 July rows with a bad-checksum GSTIN**. This structural warning does not alter the Python classification. Source Daybook narration is visible here and in the Data Quality export; the golden 80-20 Table narration stays blank. |
| 7 | Creditors & GSTIN | Enter and validate confirmed GSTINs for follow-up, then ensure they are carried into the next uploaded Creditors export so the golden calculation can use them. |
| 8 | Masters | The Python keyword list and party rules are locked. Only the Python script's interactive Fixed/Parent Group selection changes source rows; leave the July selection unchanged for the parity demonstration. |
| 9 | Audit trail | Who changed what, when. This is what explains a movement in the percentage to an auditor. |

The folder contains only genuine July inputs. Do not present a second run of July
as another month: it supersedes the earlier July draft. A synthetic August
date-shift is used in automated testing, not as an Oswal August result.

---

## Other options

```
demo.bat -Reset            start over with an empty database
demo.bat -Port 9000        use a different port if 8080 is taken
./demo.sh --reset
./demo.sh --port 9000
```

To reset completely, stop the server and delete the `var` folder — that is the
entire demo database.

---

## If something goes wrong

**"Python was not found"** — install Python 3.11 or newer from
[python.org](https://www.python.org/downloads/windows/) and tick *Add python.exe
to PATH* during setup. On macOS: `brew install python@3.12`.

**"Port already in use"** — something else is on 8080. Use `-Port 9000` /
`--port 9000` and open `http://127.0.0.1:9000`.

**The browser did not open** — the server is still running. Type the address in
manually: `http://127.0.0.1:8080`.

**The page will not load** — check the window you started it from; any error is
printed there.

**Anything odd after fiddling with settings** — `demo.bat -Reset` /
`./demo.sh --reset` puts it back to empty, then re-seed if you want.

---

## Worth knowing before you present

- The database is a single local file at `var/finops.db`. Each client PC has its
  own database; an optional Drive folder holds dated backups only. See
  `DEPLOYMENT.md`.
- The demo password is deliberately simple. A real install generates one.
- The portal itself serves every asset and makes no outbound calls. A configured
  Google Drive client separately syncs database copies when internet is available.
- The figures on screen are computed live from the July 2026 exports, not
  hard-coded. Editing a master and re-running visibly changes them.
