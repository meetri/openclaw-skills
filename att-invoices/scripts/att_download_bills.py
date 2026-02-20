#!/usr/bin/env python3
"""
Download all AT&T bill PDFs from the billing center.

Automatically detects and switches between all accounts (wireless, internet, etc.)
to download invoices for each.

Prerequisites:
  - Chrome running with CDP on port 9222
  - Already logged in (run att_login.py first)

Flow:
  1. Navigate to account overview
  2. Discover all accounts (wireless, internet, etc.)
  3. For each account:
     a. Switch to account
     b. Click billing link (in-page, NOT direct URL)
     c. Click "See all statements"
     d. Download all bill PDFs across all pages
  4. Report results

Output: ~/invoices/att/pdfs/ATTBill_NNNN_MonYYYY.pdf

Usage:
  python3 att_download_bills.py
  python3 att_download_bills.py --only-new  # Skip bills already downloaded
"""
import asyncio, os, sys
from pathlib import Path

try:
    from playwright.async_api import async_playwright
except ImportError:
    print("Missing: pip install playwright")
    sys.exit(1)

CDP_URL = os.environ.get("ATT_CDP_URL", "http://127.0.0.1:9222")
SAVE_DIR = Path(os.environ.get("ATT_PDF_DIR", Path.home() / "invoices" / "att" / "pdfs"))
ONLY_NEW = "--only-new" in sys.argv


async def download_bill(page, row_index):
    """Expand a bill row, download its Regular PDF, collapse it."""
    rows = page.locator('.titleBill')
    count = await rows.count()
    if row_index >= count:
        return None

    row = rows.nth(row_index)
    txt = (await row.inner_text()).strip().replace('\n', ' ')
    print(f'[{row_index}] {txt}', flush=True)

    # Click to expand
    await row.click()
    await asyncio.sleep(2)

    # Find "Download PDF" button
    dl_btn = page.locator('button:has-text("Download PDF"):visible')
    if await dl_btn.count() == 0:
        print(f'  No Download PDF button', flush=True)
        return None

    # Click to open dropdown
    await dl_btn.first.click()
    await asyncio.sleep(1)

    # Click "Regular PDF" in dropdown
    reg = page.locator('text=Regular PDF')
    if await reg.count() == 0:
        print(f'  No Regular PDF option', flush=True)
        return None

    try:
        async with page.expect_download(timeout=20000) as dl_info:
            await reg.first.click()
        dl = await dl_info.value
        filename = dl.suggested_filename
        save_path = str(SAVE_DIR / filename)

        if ONLY_NEW and os.path.exists(save_path):
            print(f'  ⊘ Already exists: {filename}', flush=True)
            # Cancel download
            await dl.cancel()
        else:
            await dl.save_as(save_path)
            print(f'  ✓ {filename}', flush=True)

        await asyncio.sleep(1)
        # Collapse row
        await row.click()
        await asyncio.sleep(1)
        return filename
    except Exception as e:
        print(f'  ✗ {e}', flush=True)
        return None


async def go_to_overview(page):
    """Navigate to account overview. Returns False if not logged in."""
    if 'acctmgmt/overview' not in page.url:
        print("Navigating to account overview...", flush=True)
        await page.goto("https://www.att.com/acctmgmt/overview", wait_until="domcontentloaded")
        await asyncio.sleep(5)

    if 'signin' in page.url or 'login' in page.url:
        print("ERROR: Not logged in. Run att_login.py first.", flush=True)
        return False

    if page.url.rstrip('/') in ('https://www.att.com', 'http://www.att.com'):
        print("ERROR: Redirected to homepage. Session expired.", flush=True)
        return False

    return True


async def discover_accounts(page):
    """Find all account tiles on the overview page. Returns list of {id, type, element_text}."""
    accounts = []
    # AT&T shows account tiles as divs with class containing 'nopad round text-center'
    # Each has the account number and type (Wireless, Internet, etc.)
    tiles = page.locator('div.jsx-861ccca5379a9b62')
    count = await tiles.count()

    if count == 0:
        # Fallback: look for any element with account-like numbers
        # The active account is shown in the page content
        text = await page.inner_text('body')
        print("No account tiles found — using current account only", flush=True)
        return [{"id": "current", "type": "unknown"}]

    for i in range(count):
        tile = tiles.nth(i)
        txt = (await tile.inner_text()).strip()
        lines = [l.strip() for l in txt.split('\n') if l.strip()]
        acct_id = lines[0] if lines else "unknown"
        acct_type = lines[1] if len(lines) > 1 else "unknown"
        accounts.append({"id": acct_id, "type": acct_type, "index": i})
        print(f"  Found account: {acct_id} ({acct_type})", flush=True)

    return accounts


async def switch_to_account(page, account):
    """Switch to a specific account by clicking its tile."""
    if account.get("id") == "current":
        return True

    tile = page.locator(f'text={account["id"]}').first
    if await tile.count() == 0:
        print(f"  Could not find account tile for {account['id']}", flush=True)
        return False

    await tile.click()
    print(f"  Switched to account {account['id']} ({account['type']})", flush=True)
    await asyncio.sleep(5)
    return True


async def navigate_to_bill_history(page):
    """From account overview, navigate to bill history page."""

    if not await go_to_overview(page):
        return False

    print(f"On: {page.url}", flush=True)

    # Click billing link (MUST be in-page click, not direct navigation)
    billing_link = page.locator('a[href="/acctmgmt/billing/mybillingcenter"]').nth(1)
    if await billing_link.count() == 0:
        billing_link = page.locator('a[href="/acctmgmt/billing/mybillingcenter"]').first

    await billing_link.click()
    print("Clicked billing link", flush=True)
    await asyncio.sleep(8)

    # Click "See all statements"
    see_all = page.locator('a:has-text("See all statements")')
    if await see_all.count() > 0:
        await see_all.first.click()
        print("Clicked 'See all statements'", flush=True)
        await asyncio.sleep(5)
    else:
        print("'See all statements' not found — may already be on history page", flush=True)

    if 'billandpaymenthistory' in page.url:
        print(f"On bill history page: {page.url}", flush=True)
        return True
    else:
        print(f"WARNING: Expected bill history, got: {page.url}", flush=True)
        return True


async def download_all_bills_for_current_account(page):
    """Download all bill PDFs for whichever account is currently active."""
    saved = []
    page_num = 1
    seen_bills = set()

    while True:
        rows = page.locator('.titleBill')
        count = await rows.count()
        print(f'\n  Page {page_num}: {count} bills', flush=True)

        if count == 0:
            print("  No bills found on this page.", flush=True)
            break

        # Detect loops — check if first bill on this page was already seen
        first_txt = (await rows.nth(0).inner_text()).strip().replace('\n', ' ')
        if first_txt in seen_bills:
            print("  Loop detected — already processed this page. Stopping.", flush=True)
            break
        for i in range(count):
            seen_bills.add((await rows.nth(i).inner_text()).strip().replace('\n', ' '))

        # Check if first row is already expanded
        dl_vis = page.locator('button:has-text("Download PDF"):visible')
        start_idx = 0
        if await dl_vis.count() > 0:
            print(f'  [0] Already expanded', flush=True)
            await dl_vis.first.click()
            await asyncio.sleep(1)
            reg = page.locator('text=Regular PDF')
            if await reg.count() > 0:
                try:
                    async with page.expect_download(timeout=20000) as dl_info:
                        await reg.first.click()
                    dl = await dl_info.value
                    save_path = str(SAVE_DIR / dl.suggested_filename)
                    if ONLY_NEW and os.path.exists(save_path):
                        print(f'    ⊘ Already exists: {dl.suggested_filename}', flush=True)
                        await dl.cancel()
                    else:
                        await dl.save_as(save_path)
                        print(f'    ✓ {dl.suggested_filename}', flush=True)
                    saved.append(dl.suggested_filename)
                except Exception as e:
                    print(f'    ✗ {e}', flush=True)
            await asyncio.sleep(1)
            first_row = rows.nth(0)
            await first_row.click()
            await asyncio.sleep(1)
            start_idx = 1

        for i in range(start_idx, count):
            name = await download_bill(page, i)
            if name:
                saved.append(name)

        # Check for next page
        next_link = page.locator('a:has-text("Next")')
        if await next_link.count() > 0:
            await next_link.first.click()
            await asyncio.sleep(5)
            page_num += 1
        else:
            break

    return saved


async def main():
    SAVE_DIR.mkdir(parents=True, exist_ok=True)

    pw = await async_playwright().start()
    try:
        browser = await pw.chromium.connect_over_cdp(CDP_URL)
    except Exception as e:
        print(f"ERROR: Cannot connect to Chrome CDP at {CDP_URL}: {e}")
        print("Run setup_chrome.sh first.")
        await pw.stop()
        sys.exit(1)

    page = browser.contexts[0].pages[0]

    # Go to account overview to discover all accounts
    if not await go_to_overview(page):
        await pw.stop()
        sys.exit(1)

    accounts = await discover_accounts(page)
    print(f"\nFound {len(accounts)} account(s)", flush=True)

    all_saved = {}

    for acct in accounts:
        acct_label = f"{acct['id']} ({acct.get('type', '?')})"
        print(f"\n{'=' * 50}", flush=True)
        print(f"Account: {acct_label}", flush=True)
        print(f"{'=' * 50}", flush=True)

        # Switch to this account (go to overview first)
        if not await go_to_overview(page):
            print(f"  Failed to navigate to overview for {acct_label}", flush=True)
            continue

        if not await switch_to_account(page, acct):
            print(f"  Failed to switch to {acct_label}", flush=True)
            continue

        # Navigate to bill history for this account
        if not await navigate_to_bill_history(page):
            print(f"  Failed to reach bill history for {acct_label}", flush=True)
            continue

        # Download all bills
        saved = await download_all_bills_for_current_account(page)
        all_saved[acct_label] = saved
        print(f"\n  {acct_label}: {len(saved)} bills downloaded", flush=True)

    # Summary
    print(f"\n{'=' * 50}", flush=True)
    print("SUMMARY", flush=True)
    print(f"{'=' * 50}", flush=True)
    total = 0
    for acct_label, saved in all_saved.items():
        print(f"\n{acct_label}: {len(saved)} bills", flush=True)
        for f in sorted(saved):
            print(f"  📄 {f}", flush=True)
        total += len(saved)

    print(f"\nTotal: {total} bills downloaded to {SAVE_DIR}", flush=True)

    # List all PDFs in save dir
    all_pdfs = sorted(SAVE_DIR.glob("*.pdf"))
    if all_pdfs:
        print(f"\nAll PDFs in {SAVE_DIR}:", flush=True)
        for p in all_pdfs:
            size_kb = p.stat().st_size / 1024
            print(f"  {p.name} ({size_kb:.0f} KB)", flush=True)

    await pw.stop()


asyncio.run(main())
