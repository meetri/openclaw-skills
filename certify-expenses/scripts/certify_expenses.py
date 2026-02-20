#!/usr/bin/env python3
"""
Certify/Emburse Expense Report Automation.

Creates expense reports, adds line items, uploads receipts to wallet,
attaches receipts to expenses, and submits for approval.

Requires: Active Certify session (run certify_login.py first)

Usage:
  # Create expense report with line items from config
  python3 certify_expenses.py create-report

  # Upload receipts to wallet
  python3 certify_expenses.py upload-receipts --receipts /path/to/invoice1.pdf /path/to/invoice2.pdf

  # Attach wallet receipts to expenses (interactive — matches by date)
  python3 certify_expenses.py attach-receipts --report-id <ID>

  # Submit report for approval
  python3 certify_expenses.py submit --report-id <ID>

  # Full flow: create + upload + attach + submit
  python3 certify_expenses.py full --receipts /path/to/*.pdf
"""
import os, sys, time, random, json, argparse, glob
from pathlib import Path
from datetime import datetime, timedelta

try:
    from playwright.sync_api import sync_playwright
    from playwright_stealth import Stealth
except ImportError as e:
    print(f"Missing dependency: {e}")
    print("Install: pip install playwright playwright-stealth")
    sys.exit(1)

CONFIG_FILE = Path(os.environ.get(
    "CERTIFY_CONFIG",
    Path.home() / ".config" / "certify-expenses" / "config.json"
))

# ─── Config ──────────────────────────────────────────────────────────────────

def load_config():
    cfg = {
        "cdp_url": "http://127.0.0.1:9222",
        "output_dir": str(Path.home() / "expenses" / "certify"),
        "chrome_profile": str(Path.home() / ".certify-chrome-profile"),
        # Expense defaults (all overridable)
        "category": "Cellphone & Internet",
        "vendor": "",
        "location": "",
        "monthly_limit": 120.00,
        "line_items": [
            {"description": "Cellphone", "amount": 100.00},
            {"description": "Internet",  "amount": 20.00},
        ],
        # How many months back to create expenses for (from today)
        "months_back": 2,
        "reimbursable": True,
    }
    if CONFIG_FILE.exists():
        cfg.update(json.loads(CONFIG_FILE.read_text()))
    # Env overrides
    for env_key, cfg_key in {
        "CERTIFY_CDP_URL": "cdp_url",
        "CERTIFY_OUTPUT_DIR": "output_dir",
        "CERTIFY_CATEGORY": "category",
        "CERTIFY_VENDOR": "vendor",
        "CERTIFY_LOCATION": "location",
        "CERTIFY_MONTHLY_LIMIT": "monthly_limit",
    }.items():
        val = os.environ.get(env_key)
        if val:
            cfg[cfg_key] = float(val) if cfg_key == "monthly_limit" else val
    return cfg

_cfg = load_config()
CDP_URL = _cfg["cdp_url"]
OUTPUT_DIR = Path(_cfg["output_dir"])
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ─── Helpers ─────────────────────────────────────────────────────────────────

def human_delay(low=0.3, high=1.5):
    time.sleep(random.uniform(low, high))


def connect_browser():
    """Connect to Chrome via CDP. Returns (pw, page)."""
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.connect_over_cdp(CDP_URL)
    except Exception as e:
        print(f"ERROR: Cannot connect to Chrome CDP at {CDP_URL}: {e}")
        print("Run setup_chrome.sh and certify_login.py first.")
        pw.stop()
        sys.exit(1)
    ctx = browser.contexts[0]
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    Stealth().apply_stealth_sync(page)
    return pw, page


def ensure_logged_in(page):
    """Navigate to Certify and verify we're logged in."""
    page.goto("https://expense.certify.com/ExpRptList.aspx", wait_until="domcontentloaded")
    time.sleep(3)
    if "login" in page.url.lower() or "signin" in page.url.lower():
        print("ERROR: Not logged in. Run certify_login.py first.")
        return False
    return True


def wait_for_postback(page, timeout=10):
    """Wait for ASP.NET postback to complete."""
    time.sleep(1)
    try:
        page.wait_for_load_state("networkidle", timeout=timeout * 1000)
    except Exception:
        pass
    time.sleep(1)


def select_category(page, category):
    """
    Select expense category from ASP.NET dropdown.
    CRITICAL: This triggers a postback that reloads parts of the form.
    Must be done BEFORE filling amount.
    """
    cat_select = page.query_selector('#MainContent_ExpEdit_ExpCat')
    if not cat_select:
        print("  WARNING: Category dropdown not found")
        return False

    # Find matching option
    options = page.query_selector_all('#MainContent_ExpEdit_ExpCat option')
    target_val = None
    for opt in options:
        text = opt.text_content().strip()
        if category.lower() in text.lower():
            target_val = opt.get_attribute('value')
            print(f"  Category matched: '{text}' (value={target_val})")
            break

    if not target_val:
        print(f"  WARNING: Category '{category}' not found in dropdown")
        return False

    page.select_option('#MainContent_ExpEdit_ExpCat', target_val)
    print("  Waiting for postback after category select...")
    wait_for_postback(page, timeout=15)
    return True


def fill_amount(page, amount):
    """
    Fill the Infragistics amount field.
    CRITICAL: Must use keyboard.type() + Tab, NOT fill().
    The Infragistics WebNumericEditor ignores programmatic fill().
    """
    amount_input = page.query_selector(
        '#igtxtMainContent_ExpEdit_Amount_wneValue, '
        'input[id*="Amount"][id*="wneValue"]'
    )
    if not amount_input:
        print("  WARNING: Amount input not found (postback may not have completed)")
        return False

    amount_input.click()
    human_delay(0.2, 0.5)
    # Clear existing value
    page.keyboard.press("Control+a")
    page.keyboard.press("Delete")
    human_delay(0.1, 0.3)
    # Type the amount
    page.keyboard.type(f"{amount:.2f}")
    human_delay(0.2, 0.5)
    # Tab out to trigger Infragistics validation
    page.keyboard.press("Tab")
    human_delay(0.3, 0.6)
    return True


def fill_vendor(page, vendor):
    """
    Fill vendor autocomplete field.
    Must type text, wait for suggestion dropdown, then click the suggestion.
    """
    vendor_input = page.query_selector(
        '#MainContent_ExpEdit_Exp_Vendor_ccsuggestselection_tb_Exp_Vendor, '
        'input[id*="Vendor"][id*="suggestselection"]'
    )
    if not vendor_input:
        print("  WARNING: Vendor input not found")
        return False

    vendor_input.click()
    human_delay(0.2, 0.5)
    vendor_input.fill("")
    vendor_input.type(vendor, delay=random.randint(50, 80))
    time.sleep(2)  # Wait for autocomplete

    # Click suggestion
    suggestion = page.query_selector(
        f'div.suggestions div:has-text("{vendor}"), '
        f'div[id*="suggest"] div:has-text("{vendor}"), '
        f'.ac_results li:has-text("{vendor}")'
    )
    if suggestion:
        suggestion.click()
        human_delay()
    else:
        # No autocomplete — just tab out
        page.keyboard.press("Tab")
        human_delay()
    return True


def fill_location(page, location):
    """Fill location autocomplete field (similar to vendor)."""
    loc_input = page.query_selector(
        '#MainContent_ExpEdit_Exp_Location_ccsuggestselection_tb_Exp_Location, '
        'input[id*="Location"][id*="suggestselection"]'
    )
    if not loc_input:
        print("  WARNING: Location input not found")
        return False

    loc_input.click(force=True)  # Force click — sometimes obscured
    human_delay(0.2, 0.5)
    loc_input.fill("")
    loc_input.type(location, delay=random.randint(50, 80))
    time.sleep(2)

    suggestion = page.query_selector(
        f'div.suggestions div:has-text("{location}"), '
        f'div[id*="suggest"] div:has-text("{location}"), '
        f'.ac_results li:has-text("{location}")'
    )
    if suggestion:
        suggestion.click()
        human_delay()
    else:
        page.keyboard.press("Tab")
        human_delay()
    return True


def fill_date(page, date_str):
    """Fill the expense date field (MM/DD/YYYY format)."""
    date_input = page.query_selector(
        '#MainContent_ExpEdit_Exp_Date_input, input[id*="Exp_Date"]'
    )
    if not date_input:
        print("  WARNING: Date input not found")
        return False

    date_input.click()
    human_delay(0.2, 0.5)
    page.keyboard.press("Control+a")
    page.keyboard.type(date_str)
    page.keyboard.press("Tab")
    human_delay()
    return True


# ─── Commands ────────────────────────────────────────────────────────────────

def cmd_create_report(args):
    """Create an expense report with line items."""
    pw, page = connect_browser()

    try:
        if not ensure_logged_in(page):
            return False

        # Calculate date range for report name
        today = datetime.now()
        months_back = int(args.months_back or _cfg["months_back"])
        start_date = today - timedelta(days=months_back * 30)
        report_name = f"Expenses - {start_date.strftime('%-m/%-d/%Y')} - {today.strftime('%-m/%-d/%Y')}"

        print(f"Creating report: {report_name}")

        # Navigate to create new report
        page.goto("https://expense.certify.com/ExpRptView.aspx", wait_until="domcontentloaded")
        time.sleep(3)
        page.screenshot(path=str(OUTPUT_DIR / "step_new_report.png"))

        # The report is auto-created; now add expenses
        # Generate line items for each month
        line_items = args.line_items if hasattr(args, 'line_items') and args.line_items else _cfg["line_items"]
        category = args.category or _cfg["category"]
        vendor = args.vendor or _cfg["vendor"]
        location = args.location or _cfg["location"]

        total = 0.0
        monthly_limit = float(_cfg["monthly_limit"])

        for month_offset in range(months_back):
            month_date = today - timedelta(days=(month_offset + 1) * 30)
            month_total = 0.0

            for item in line_items:
                if month_total + item["amount"] > monthly_limit:
                    print(f"  Skipping {item['description']} — would exceed ${monthly_limit}/mo limit")
                    continue

                # Determine expense date (5th for cellphone, 17th for internet — convention)
                if "cell" in item["description"].lower() or "phone" in item["description"].lower():
                    exp_day = 5
                elif "internet" in item["description"].lower():
                    exp_day = 17
                else:
                    exp_day = 15
                exp_date = month_date.replace(day=min(exp_day, 28))
                date_str = exp_date.strftime("%-m/%-d/%Y")

                print(f"\n  Adding expense: {date_str} — {item['description']} — ${item['amount']:.2f}")

                # Click "Add Expense" button
                add_btn = page.query_selector(
                    'a:has-text("Add Expense"), input[value*="Add Expense"], '
                    '#MainContent_btnAddExpense, a[id*="AddExpense"]'
                )
                if add_btn:
                    add_btn.click()
                    time.sleep(3)
                    page.wait_for_load_state("domcontentloaded")

                # Fill fields in order: Date → Category (triggers postback) → Amount → Vendor → Location
                fill_date(page, date_str)

                # Category MUST be selected before amount (postback reveals amount field)
                if not select_category(page, category):
                    print("  ERROR: Could not select category")
                    continue

                if not fill_amount(page, item["amount"]):
                    print("  ERROR: Could not fill amount")
                    continue

                fill_vendor(page, vendor)
                fill_location(page, location)

                # Save the expense
                save_btn = page.query_selector(
                    '#MainContent_ExpEdit_btnSave, input[value="Save"], '
                    'a:has-text("Save"), button:has-text("Save")'
                )
                if save_btn:
                    save_btn.click()
                    wait_for_postback(page, timeout=10)
                    print(f"  ✓ Saved")

                month_total += item["amount"]
                total += item["amount"]

        print(f"\n{'='*50}")
        print(f"Total expenses added: ${total:.2f}")
        print(f"Report URL: {page.url}")
        page.screenshot(path=str(OUTPUT_DIR / "step_report_complete.png"))

        # Save report ID for later
        report_id = page.url.split("ID=")[-1] if "ID=" in page.url else "unknown"
        (OUTPUT_DIR / "last_report_id.txt").write_text(report_id)
        print(f"Report ID saved to {OUTPUT_DIR / 'last_report_id.txt'}")

        return True

    except Exception as e:
        print(f"Exception: {e}", flush=True)
        try:
            page.screenshot(path=str(OUTPUT_DIR / "error_create.png"))
        except Exception:
            pass
        return False
    finally:
        pw.stop()


def cmd_upload_receipts(args):
    """Upload receipt PDFs to the Certify Wallet."""
    receipts = args.receipts
    if not receipts:
        print("No receipts specified. Use --receipts /path/to/*.pdf")
        return False

    # Expand globs
    files = []
    for r in receipts:
        files.extend(glob.glob(r))
    files = [f for f in files if os.path.isfile(f)]

    if not files:
        print(f"No files found matching: {receipts}")
        return False

    print(f"Uploading {len(files)} receipt(s) to Certify Wallet...")

    pw, page = connect_browser()
    try:
        if not ensure_logged_in(page):
            return False

        # Navigate to Add Receipts / Wallet page
        page.goto("https://expense.certify.com/AddReceipts.aspx", wait_until="domcontentloaded")
        time.sleep(3)
        page.screenshot(path=str(OUTPUT_DIR / "step_wallet.png"))

        for filepath in files:
            filename = os.path.basename(filepath)
            print(f"\n  Uploading: {filename}")

            # Find file input (may be hidden — use set_input_files which works even on hidden inputs)
            file_input = page.query_selector(
                '#MainContent_CertifyWalletSelect_FileUpload2, '
                'input[type="file"], '
                'input[id*="FileUpload"]'
            )
            if not file_input:
                print(f"  ERROR: No file input found on page")
                page.screenshot(path=str(OUTPUT_DIR / f"error_no_file_input.png"))
                continue

            # Set file (works even on hidden inputs)
            file_input.set_input_files(filepath)
            time.sleep(2)

            # Click upload button
            upload_btn = page.query_selector(
                '#MainContent_CertifyWalletSelect_btnUploadMini, '
                'input[value*="Upload"], button:has-text("Upload"), '
                'a:has-text("Upload")'
            )
            if upload_btn:
                # Make visible if hidden (ASP.NET sometimes hides it)
                page.evaluate("""btn => {
                    btn.style.display = 'inline-block';
                    btn.style.visibility = 'visible';
                    btn.style.opacity = '1';
                }""", upload_btn)
                time.sleep(0.5)
                upload_btn.click()
                wait_for_postback(page, timeout=15)
                print(f"  ✓ Uploaded: {filename}")
            else:
                # Try form submit as fallback
                page.evaluate("document.forms[0].submit()")
                wait_for_postback(page, timeout=15)
                print(f"  ✓ Uploaded (form submit): {filename}")

            page.screenshot(path=str(OUTPUT_DIR / f"step_uploaded_{filename}.png"))
            time.sleep(2)

        print(f"\nAll receipts uploaded to wallet.")
        return True

    except Exception as e:
        print(f"Exception: {e}", flush=True)
        try:
            page.screenshot(path=str(OUTPUT_DIR / "error_upload.png"))
        except Exception:
            pass
        return False
    finally:
        pw.stop()


def cmd_attach_receipts(args):
    """Attach wallet receipts to expense line items in a report."""
    report_id = args.report_id or (OUTPUT_DIR / "last_report_id.txt").read_text().strip()
    if not report_id or report_id == "unknown":
        print("No report ID. Use --report-id <ID> or create a report first.")
        return False

    print(f"Attaching receipts to report {report_id}...")

    pw, page = connect_browser()
    try:
        if not ensure_logged_in(page):
            return False

        # Navigate to the report
        page.goto(f"https://expense.certify.com/ExpRptView.aspx?ID={report_id}",
                   wait_until="domcontentloaded")
        time.sleep(3)

        # Find expense rows
        expense_rows = page.query_selector_all('tr[id*="MainContent_gvExpenses"]')
        if not expense_rows:
            # Try alternate selector
            expense_rows = page.query_selector_all('.expense-row, tr.gridRow, tr.gridAltRow')

        print(f"  Found {len(expense_rows)} expense(s)")

        for i, row in enumerate(expense_rows):
            # Check if receipt already attached
            receipt_status = row.query_selector('.receipt-status, td:has-text("No Receipt")')
            if not receipt_status:
                continue

            text = receipt_status.text_content().strip()
            if "no receipt" not in text.lower():
                print(f"  Row {i+1}: Receipt already attached, skipping")
                continue

            print(f"  Row {i+1}: Attaching receipt...")

            # Click on the expense to edit it
            edit_link = row.query_selector('a[id*="EditItem"], a:has-text("Edit")')
            if edit_link:
                edit_link.click()
                time.sleep(3)
                page.wait_for_load_state("domcontentloaded")

            # Look for "Select Receipt" button/link
            receipt_btn = page.query_selector(
                'a:has-text("Select Receipt"), a:has-text("Add Receipt"), '
                'input[value*="Receipt"], #MainContent_ExpEdit_btnReceipt'
            )
            if receipt_btn:
                receipt_btn.click()
                time.sleep(3)

                # Receipt selection modal should appear
                # Click the first available wallet receipt
                wallet_item = page.query_selector(
                    '.wallet-item, .receipt-thumb, div[id*="Wallet"] img, '
                    'div[id*="receipt"] a, .rv_item'
                )
                if wallet_item:
                    wallet_item.click()
                    time.sleep(2)

                    # Confirm selection
                    select_btn = page.query_selector(
                        'button:has-text("Select"), input[value="Select"], '
                        'a:has-text("Use This"), button:has-text("Attach")'
                    )
                    if select_btn:
                        select_btn.click()
                        wait_for_postback(page)
                        print(f"  ✓ Receipt attached to row {i+1}")

            # Save and go back to report
            save_btn = page.query_selector(
                '#MainContent_ExpEdit_btnSave, input[value="Save"]'
            )
            if save_btn:
                save_btn.click()
                wait_for_postback(page)

        page.screenshot(path=str(OUTPUT_DIR / "step_receipts_attached.png"))
        return True

    except Exception as e:
        print(f"Exception: {e}", flush=True)
        try:
            page.screenshot(path=str(OUTPUT_DIR / "error_attach.png"))
        except Exception:
            pass
        return False
    finally:
        pw.stop()


def cmd_submit(args):
    """Submit an expense report for approval."""
    report_id = args.report_id or (OUTPUT_DIR / "last_report_id.txt").read_text().strip()
    if not report_id or report_id == "unknown":
        print("No report ID. Use --report-id <ID>")
        return False

    if not args.confirm:
        print(f"This will SUBMIT report {report_id} for approval.")
        print("Add --confirm to proceed.")
        return False

    print(f"Submitting report {report_id} for approval...")

    pw, page = connect_browser()
    try:
        if not ensure_logged_in(page):
            return False

        page.goto(f"https://expense.certify.com/ExpRptView.aspx?ID={report_id}",
                   wait_until="domcontentloaded")
        time.sleep(3)

        # Override any confirm dialogs
        page.evaluate("window.customConfirm = function() { return true; }")
        page.evaluate("window.confirm = function() { return true; }")

        # Click Submit
        submit_btn = page.query_selector(
            '#MainContent_btnSubmit, input[value*="Submit"], '
            'a:has-text("Submit"), button:has-text("Submit")'
        )
        if not submit_btn:
            print("ERROR: Submit button not found")
            page.screenshot(path=str(OUTPUT_DIR / "error_no_submit.png"))
            return False

        submit_btn.click()
        time.sleep(3)

        # Handle confirmation dialog if ASP.NET uses one
        page.evaluate("window.customConfirm = function() { return true; }")

        wait_for_postback(page, timeout=15)
        page.screenshot(path=str(OUTPUT_DIR / "step_submitted.png"))

        # Verify submission
        body = (page.text_content("body") or "").lower()
        if "submitted" in body or "pending" in body or "approval" in body:
            print("✓ Report submitted for approval!")
            return True
        else:
            print(f"Submit may have succeeded. Check Certify. URL: {page.url}")
            return True

    except Exception as e:
        print(f"Exception: {e}", flush=True)
        try:
            page.screenshot(path=str(OUTPUT_DIR / "error_submit.png"))
        except Exception:
            pass
        return False
    finally:
        pw.stop()


def cmd_full(args):
    """Full flow: create report → upload receipts → attach → submit."""
    print("=" * 60)
    print("FULL EXPENSE FLOW")
    print("=" * 60)

    print("\n[1/4] Creating expense report...")
    if not cmd_create_report(args):
        print("FAILED at create-report step")
        return False

    if args.receipts:
        print("\n[2/4] Uploading receipts to wallet...")
        if not cmd_upload_receipts(args):
            print("WARNING: Receipt upload failed — continue manually")

        print("\n[3/4] Attaching receipts to expenses...")
        if not cmd_attach_receipts(args):
            print("WARNING: Receipt attach failed — continue manually")
    else:
        print("\n[2/4] No receipts specified — skipping upload")
        print("[3/4] Skipping attach")

    if args.confirm:
        print("\n[4/4] Submitting for approval...")
        return cmd_submit(args)
    else:
        print("\n[4/4] Skipping submit (add --confirm to auto-submit)")
        print("Review the report in Certify, then submit manually or run:")
        report_id = (OUTPUT_DIR / "last_report_id.txt").read_text().strip()
        print(f"  python3 certify_expenses.py submit --report-id {report_id} --confirm")
        return True


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Certify/Emburse Expense Automation")
    sub = parser.add_subparsers(dest="command", required=True)

    # create-report
    p_create = sub.add_parser("create-report", help="Create expense report with line items")
    p_create.add_argument("--months-back", type=int, default=None)
    p_create.add_argument("--category", default="")
    p_create.add_argument("--vendor", default="")
    p_create.add_argument("--location", default="")

    # upload-receipts
    p_upload = sub.add_parser("upload-receipts", help="Upload receipt PDFs to wallet")
    p_upload.add_argument("--receipts", nargs="+", required=True)

    # attach-receipts
    p_attach = sub.add_parser("attach-receipts", help="Attach wallet receipts to expenses")
    p_attach.add_argument("--report-id", default="")

    # submit
    p_submit = sub.add_parser("submit", help="Submit report for approval")
    p_submit.add_argument("--report-id", default="")
    p_submit.add_argument("--confirm", action="store_true")

    # full
    p_full = sub.add_parser("full", help="Full flow: create + upload + attach + submit")
    p_full.add_argument("--months-back", type=int, default=None)
    p_full.add_argument("--category", default="")
    p_full.add_argument("--vendor", default="")
    p_full.add_argument("--location", default="")
    p_full.add_argument("--receipts", nargs="+", default=[])
    p_full.add_argument("--report-id", default="")
    p_full.add_argument("--confirm", action="store_true")

    args = parser.parse_args()

    commands = {
        "create-report": cmd_create_report,
        "upload-receipts": cmd_upload_receipts,
        "attach-receipts": cmd_attach_receipts,
        "submit": cmd_submit,
        "full": cmd_full,
    }

    success = commands[args.command](args)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
