import asyncio
import os
import json
from playwright.async_api import async_playwright
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
USERNAME = os.getenv("EMAIL")
PASSWORD = os.getenv("PASSWORD")
LOGIN_URL = os.getenv("TARGET_URL")

async def main():
    print("🚀 Starting scraper")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=200)
        page = await browser.new_page()

        # ------------------------------
        # Step 1: Login
        # ------------------------------
        print("Step 1: Logging in...")
        await page.goto(LOGIN_URL)
        await page.get_by_label("Email").fill(USERNAME)
        await page.get_by_label("Password").fill(PASSWORD)
        await page.get_by_role("button", name="Sign in").click()
        await page.wait_for_load_state("networkidle")
        print("✅ Logged in")

        # ------------------------------
        # Step 2 → Step 8
        # ------------------------------
        await page.click("text=Launch Challenge")
        await page.wait_for_load_state("networkidle")
        await page.click("text=Options")
        await page.click("text=Inventory")
        await page.click("text=Access Detailed View")
        await page.click("text=Detailed View")
        await page.wait_for_selector("text=Show Full Product Table")
        await page.click("text=Show Full Product Table")
        await page.wait_for_selector("text=View Product Table")
        await page.click("text=View Product Table")
        print("✅ Product Table visible")

        # ------------------------------
        # Step 9: Scrape table with virtual scroll (only this part changed)
        # ------------------------------
        print("📊 Scraping product table...")

        # Ensure table exists before we start
        await page.wait_for_selector("table")

        # Helper: get headers (fallback to generic names if no thead)
        headers = await page.evaluate("""
            () => {
                const ths = Array.from(document.querySelectorAll('table thead th')).map(th => th.innerText.trim());
                if (ths.length) return ths;
                const firstRow = document.querySelector('table tbody tr');
                if (!firstRow) return [];
                const tdCount = firstRow.querySelectorAll('td').length;
                return Array.from({length: tdCount}, (_, i) => `col_${i+1}`);
            }
        """)

        seen_keys = set()
        rows_accum = []

        no_progress_rounds = 0
        last_total = 0
        attempts = 0
        safety_ceiling = 20000  # prevents infinite loop if something goes weird

        while attempts < safety_ceiling:
            attempts += 1

            # Grab ONLY currently visible rows in one roundtrip
            visible_rows = await page.evaluate("""
                () => {
                    const table = document.querySelector('table');
                    if (!table) return [];
                    const trs = Array.from(table.querySelectorAll('tbody tr'))
                        .filter(r => r.offsetParent !== null);
                    return trs.map(tr => Array.from(tr.querySelectorAll('td')).map(td => td.innerText.trim()));
                }
            """)

            # Dedup by full row content (or by first cell if you prefer strict ID)
            for cells in visible_rows:
                if not cells:
                    continue
                # Prefer first cell as unique key if present; otherwise use whole row
                key = cells[0] if cells and cells[0] else "|".join(cells)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                rows_accum.append(cells)

            # Track progress
            if len(rows_accum) > last_total:
                last_total = len(rows_accum)
                no_progress_rounds = 0
            else:
                no_progress_rounds += 1

            # Scroll the TRUE scrollable container (ancestor with overflow)
            scrolled = await page.evaluate("""
                () => {
                    const table = document.querySelector('table');
                    if (!table) return { ok: false, reason: 'no-table' };

                    function getScroller(el) {
                        let node = el;
                        while (node && node !== document.body) {
                            const s = getComputedStyle(node);
                            const oy = s.overflowY;
                            if ((oy === 'auto' || oy === 'scroll') && node.scrollHeight > node.clientHeight + 1) {
                                return node;
                            }
                            node = node.parentElement;
                        }
                        // fallback: look inside descendants (e.g., custom virtual list wrappers)
                        for (const d of table.querySelectorAll('div')) {
                            const s2 = getComputedStyle(d);
                            const oy2 = s2.overflowY;
                            if ((oy2 === 'auto' || oy2 === 'scroll') && d.scrollHeight > d.clientHeight + 1) {
                                return d;
                            }
                        }
                        // final fallback: page scroller
                        return document.scrollingElement || document.documentElement;
                    }

                    const scroller = getScroller(table);
                    if (!scroller) return { ok: false, reason: 'no-scroller' };

                    const prev = scroller.scrollTop;
                    const max  = scroller.scrollHeight - scroller.clientHeight;
                    const next = Math.min(prev + scroller.clientHeight, max);
                    scroller.scrollTop = next;

                    return { ok: true, prev, now: scroller.scrollTop, max };
                }
            """)

            # If we couldn't scroll (or we're at bottom) and we haven't seen new rows for a while -> stop
            at_bottom = False
            if scrolled and scrolled.get("ok"):
                at_bottom = scrolled["now"] >= scrolled["max"]

            if (not scrolled or not scrolled.get("ok") or scrolled["now"] == scrolled["prev"]):
                if at_bottom and no_progress_rounds >= 5:
                    print("ℹ️ Reached bottom with no new rows. Stopping.")
                    break

            # Give virtualization time to render the next batch
            await page.wait_for_timeout(200)

        # ------------------------------
        # Save structured JSON (always write a file)
        # ------------------------------
        json_data = [
            dict(zip(headers, row)) if headers else {f"col_{i+1}": v for i, v in enumerate(row)}
            for row in rows_accum
        ]

        out_path = "product_table.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(json_data, f, indent=4, ensure_ascii=False)

        print(f"✅ Scraped {len(rows_accum)} rows and saved to {out_path}")

        await browser.close()
        print("🎉 Done")

if __name__ == "__main__":
    asyncio.run(main())
