"""Exercises the parts that change data: resolutions, roles, exports, masters."""
from playwright.sync_api import sync_playwright, expect
B = "http://127.0.0.1:8080"
out = []

def login(pg, email, pw):
    pg.goto(B + "/logout")
    pg.goto(B + "/login")
    pg.fill('input[name=email]', email); pg.fill('input[name=password]', pw)
    pg.click('button[type=submit]'); pg.wait_for_load_state("domcontentloaded")

def _p(x):
    print(x, flush=True)

with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_page(viewport={"width": 1440, "height": 1000})
    errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
    login(pg, "admin@oswalgroup.net", "TestPassword123")

    # 1. create a preparer and a viewer
    pg.goto(B + "/admin/users")
    pg.fill('input[name=name]', "Sandip Das"); pg.fill('input[name=email]', "sandip@oswalgroup.net")
    pg.fill('form[action="/admin/users"] input[name=password]', "PreparerPass123")
    pg.select_option('form[action="/admin/users"] select[name=role]', "preparer")
    pg.click('form[action="/admin/users"] button[type=submit]'); pg.wait_for_timeout(500)
    pg.fill('input[name=name]', "Auditor"); pg.fill('input[name=email]', "auditor@oswalgroup.net")
    pg.fill('form[action="/admin/users"] input[name=password]', "ViewerPass1234")
    pg.select_option('form[action="/admin/users"] select[name=role]', "viewer")
    pg.click('form[action="/admin/users"] button[type=submit]'); pg.wait_for_timeout(500)
    _p(f"users created: {pg.eval_on_selector_all('table tbody tr td:nth-child(2)', 'a=>a.length')}")

    # 2. preparer resolves a rectification, entering a GSTIN
    login(pg, "sandip@oswalgroup.net", "PreparerPass123")
    pg.goto(B + "/gst8020/rectifications")
    first = pg.query_selector('details.box')
    label = first.query_selector('summary').inner_text().split("\n")[0]
    _p("resolving: " + label[:70])
    first.query_selector('summary').click(); pg.wait_for_timeout(300)
    pg.select_option('details.box select[name=status]', "resolved_registered")
    pg.fill('details.box input[name=gstin]', "19AABCB1518L1ZP")
    pg.fill('details.box textarea[name=note]', "Confirmed with the supplier; GSTIN added.")
    pg.select_option('details.box select[name=assigned_to]', index=1)
    pg.click('details.box button[type=submit]'); pg.wait_for_timeout(900)
    pg.goto(B + "/gst8020/rectifications?status=resolved_registered")
    _p(f"resolved items now: {pg.eval_on_selector_all('details.box', 'a=>a.length')}")

    # 3. a bad GSTIN must be refused
    pg.goto(B + "/gst8020/rectifications")
    f2 = pg.query_selector('details.box'); f2.query_selector('summary').click(); pg.wait_for_timeout(250)
    pg.fill('details.box input[name=gstin]', "19AABCB1518L1ZZ")
    pg.click('details.box button[type=submit]'); pg.wait_for_timeout(700)
    _p("bad GSTIN response: " + pg.inner_text("h1")[:60] + " | " +
               pg.inner_text(".sub")[:90])

    # 4. viewer must not be able to edit
    login(pg, "auditor@oswalgroup.net", "ViewerPass1234")
    pg.goto(B + "/gst8020/rectifications")
    pg.query_selector('details.box summary').click(); pg.wait_for_timeout(250)
    _p(f"viewer sees edit form: {pg.eval_on_selector_all('details.box button[type=submit]', 'a=>a.length') > 0}")
    r = pg.goto(B + "/admin/users")
    _p(f"viewer on /admin/users -> HTTP {r.status}")
    r = pg.goto(B + "/gst8020/new")
    _p(f"viewer on /gst8020/new -> HTTP {r.status}")

    # 5. export downloads
    login(pg, "admin@oswalgroup.net", "TestPassword123")
    pg.goto(B + "/gst8020")
    for url, dest in [("/gst8020/runs/1/export.xlsx", "/home/claude/out/80-20_Jul26_from_app.xlsx"),
                      ("/gst8020/creditors/missing-gstin.xlsx", "/home/claude/out/GSTIN_to_complete.xlsx")]:
        resp = pg.request.get(B + url)
        open(dest, "wb").write(resp.body())
        _p(f"{url} -> HTTP {resp.status}, {len(resp.body()):,} bytes")

    # 6. masters save
    pg.goto(B + "/gst8020/masters")
    pg.fill('input[name=alert_buffer_pct]', "83")
    pg.click('button[type=submit]'); pg.wait_for_timeout(600)
    _p("alert buffer now: " + pg.input_value('input[name=alert_buffer_pct]'))

    # 7. audit trail recorded it all
    pg.goto(B + "/admin/audit")
    _p("audit rows: " + str(pg.eval_on_selector_all('table tbody tr', 'a=>a.length')))
    _p("actions seen: " + ", ".join(sorted(set(
        pg.eval_on_selector_all('table tbody tr td:nth-child(3)', 'a=>a.map(e=>e.innerText)')))))
    pg.screenshot(path="/home/claude/out/p8_audit.png", full_page=True)

    print("JS ERRORS:", errs or "none")
    b.close()
