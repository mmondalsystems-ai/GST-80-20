"""End-to-end walkthrough of the portal against the real July 2026 exports."""
from playwright.sync_api import sync_playwright
M = "/home/claude/msg/"
BASE = "http://127.0.0.1:8080"
errs = []

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1440, "height": 1000})
    pg.on("pageerror", lambda e: errs.append("JS " + str(e)))

    def go(path):
        r = pg.goto(BASE + path, wait_until="domcontentloaded")
        if r.status >= 400:
            errs.append(f"{path} -> HTTP {r.status}")
        return r

    go("/")
    pg.fill('input[name=email]', "admin@oswalgroup.net")
    pg.fill('input[name=password]', "TestPassword123")
    pg.click('button[type=submit]')
    pg.wait_for_load_state("domcontentloaded")
    print("after login:", pg.url)
    pg.screenshot(path="/home/claude/out/p1_portal.png", full_page=True)

    go("/gst8020/new")
    pg.set_input_files('input[name=daybook]', M + "DayBookRegister.xlsx")
    pg.set_input_files('input[name=voucher]', M + "Search Voucher.xlsx")
    pg.set_input_files('input[name=creditors]', M + "Creditors Details.xlsx")
    pg.fill('input[name=label]', "July 2026 — first pass")
    pg.click('button[type=submit]')
    pg.wait_for_load_state("domcontentloaded", timeout=180000)
    print("after run:", pg.url)
    print("HEADLINE:", pg.inner_text(".bignum"), "|", pg.inner_text(".verdict"))
    print("STATS:", " / ".join(pg.inner_text(".stats").split("\n")))
    pg.screenshot(path="/home/claude/out/p2_run.png", full_page=True)
    run_url = pg.url

    for path, name in [("/gst8020", "p3_overview"), ("/gst8020/year", "p4_year"),
                       ("/gst8020/rectifications", "p5_rect"),
                       ("/gst8020/creditors", "p6_creditors"),
                       ("/gst8020/masters", "p7_masters"),
                       ("/admin/audit", "p8_audit"), ("/admin/users", "p9_users"),
                       ("/gst8020/overrides", "p10_overrides")]:
        go(path)
        pg.screenshot(path=f"/home/claude/out/{name}.png", full_page=True)

    rid = run_url.rstrip("/").split("/")[-1]
    for path, name in [(f"/gst8020/runs/{rid}/rows", "p11_rows"),
                       (f"/gst8020/runs/{rid}/vendors", "p12_vendors"),
                       (f"/gst8020/runs/{rid}/exceptions", "p13_exceptions")]:
        go(path)
        pg.screenshot(path=f"/home/claude/out/{name}.png", full_page=True)

    go(f"/gst8020/runs/{rid}/rows?status=Needs+Rectification&elig=Eligible")
    print("FILTERED ROWS:", pg.inner_text(".page-head .sub"))

    print("ERRORS:", errs or "none")
    b.close()
