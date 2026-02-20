---
name: certify-expenses
description: Automate Certify/Emburse expense reports — login, create expenses, upload receipts to wallet, attach to expenses, and submit for approval. Use when the user asks about expense reports, reimbursement, Certify, or Emburse.
---

# Certify/Emburse Expense Report Automation

Automates the full expense report lifecycle on Certify (Emburse) via headless Chrome + Playwright CDP:

1. **Login** with MFA (SMS code)
2. **Create expense report** with configurable line items
3. **Upload receipts** (PDFs) to Certify Wallet
4. **Attach receipts** from wallet to individual expenses
5. **Submit report** for approval

## Architecture

Same approach as att-invoices — real Chrome on Xvfb to avoid bot detection:

```
Xvfb (virtual display :99)
  └─ Real Chrome (non-headless, --remote-debugging-port=9222)
       └─ Playwright connects via CDP
            └─ playwright-stealth patches applied
```

Chrome runs persistently in a tmux session. Scripts connect/disconnect via CDP without killing Chrome.

## Prerequisites

### Software
- `google-chrome-stable`
- `xvfb`, `tmux`
- Python: `playwright`, `playwright-stealth`
- `pass` (GPG-encrypted password store)

### Credentials

Store in `pass`:
```bash
pass insert certify/login
# Line 1: password
# Line 2: user: your@email.com
```

Verify: `pass show certify/login`

### Configuration

All settings configurable via config file, env vars, or CLI args.

**Config file** (`~/.config/certify-expenses/config.json`):
```json
{
  "cdp_url": "http://127.0.0.1:9222",
  "output_dir": "/home/user/expenses/certify",
  "mfa_phone": "XXXX",
  "mfa_timeout": 300,
  "chrome_profile": "/home/user/.certify-chrome-profile",
  "pass_path": "certify/login",
  "category": "Cellphone & Internet",
  "vendor": "Your Vendor",
  "location": "Your City, ST",
  "monthly_limit": 120.00,
  "line_items": [
    {"description": "Cellphone", "amount": 100.00},
    {"description": "Internet", "amount": 20.00}
  ],
  "months_back": 2,
  "reimbursable": true
}
```

**Environment variables**: `CERTIFY_CDP_URL`, `CERTIFY_OUTPUT_DIR`, `CERTIFY_MFA_PHONE`, `CERTIFY_MFA_TIMEOUT`, `CERTIFY_CHROME_PROFILE`, `CERTIFY_PASS_PATH`, `CERTIFY_CATEGORY`, `CERTIFY_VENDOR`, `CERTIFY_LOCATION`, `CERTIFY_MONTHLY_LIMIT`

**CLI args**: `--mfa-phone=XXXX`, `--timeout=N`, `--output-dir=...`, `--cdp-url=...`, `--months-back=N`, `--category=...`, `--vendor=...`, `--location=...`

### MFA
Certify requires MFA via SMS on login. The agent must ask the human for the verification code.

## Step-by-Step Flow

### Phase 1: Infrastructure Setup (one-time)

```bash
bash scripts/setup_chrome.sh
```

Or if sharing with att-invoices (same Chrome instance):
```bash
# Just verify Chrome is running
curl -s http://127.0.0.1:9222/json/version | head -5
```

### Phase 2: Login + MFA

```bash
python3 scripts/certify_login.py
```

1. Navigates to `https://expense.certify.com`
2. Enters email → password
3. Sends MFA code to configured phone
4. **Writes status to `~/expenses/certify/status.txt`**
5. Polls `~/expenses/certify/mfa_code.txt` for the code
6. Submits code, waits for dashboard

**Agent must ask the human for the MFA code and write it to `~/expenses/certify/mfa_code.txt`.**

### Phase 3: Create Expense Report

```bash
python3 scripts/certify_expenses.py create-report
python3 scripts/certify_expenses.py create-report --months-back 3 --category "Travel"
```

Creates a new expense report and adds line items for each month. Default: $100 cellphone + $20 internet × N months = $120/month.

**Monthly limit enforced**: Line items exceeding `monthly_limit` ($120 default) are skipped.

### Phase 4: Upload Receipts to Wallet

```bash
python3 scripts/certify_expenses.py upload-receipts \
  --receipts /path/to/invoices/*.pdf
```

Uploads PDFs to the Certify Wallet (AddReceipts.aspx). Receipts live in the wallet until attached to expenses.

### Phase 5: Attach Receipts to Expenses

```bash
python3 scripts/certify_expenses.py attach-receipts --report-id <ID>
```

Iterates through expense line items in the report, finds ones with "No Receipt", and attaches wallet receipts.

### Phase 6: Submit for Approval

```bash
python3 scripts/certify_expenses.py submit --report-id <ID> --confirm
```

Submits the report. `--confirm` is required as a safety gate.

### Full Flow (All-in-One)

```bash
python3 scripts/certify_expenses.py full \
  --receipts /path/to/invoices/*.pdf \
  --months-back 2 \
  --confirm
```

Runs create → upload → attach → submit in sequence.

## Output

- Screenshots at each step: `~/expenses/certify/step_*.png`
- Error screenshots: `~/expenses/certify/error_*.png`
- Status file: `~/expenses/certify/status.txt`
- Last report ID: `~/expenses/certify/last_report_id.txt`

## Critical Lessons (Hard-Won from Certify's ASP.NET UI)

### 1. Category Select Triggers Postback
Selecting a category causes an ASP.NET postback that reloads parts of the form. **You MUST select category BEFORE filling the amount field.** If you fill amount first, the postback wipes it.

### 2. Infragistics Amount Field
The amount input is a WebNumericEditor (Infragistics). Standard `fill()` does NOT work. You must:
```python
input.click()
page.keyboard.press("Control+a")
page.keyboard.press("Delete")
page.keyboard.type("100.00")
page.keyboard.press("Tab")  # Triggers validation
```

### 3. Vendor Autocomplete
The vendor field uses a custom autocomplete. Type the name, wait 2 seconds for the suggestion dropdown, then click the matching `div` in the suggestions list. If no suggestion appears, Tab out.

### 4. Location Requires Force Click
The location field is sometimes obscured by other elements. Use `force=True` on the click.

### 5. Receipt Upload — File Input Visibility
On AddReceipts.aspx, the file input (`MainContent_CertifyWalletSelect_FileUpload2`) may be hidden. Use `set_input_files()` which works on hidden inputs. The upload button may also be hidden after postback — force-show it via JS before clicking.

### 6. ASP.NET Postbacks Are Everywhere
Nearly every action triggers a `__doPostBack`. Always wait for `networkidle` after clicks. Don't assume form state persists across interactions.

### 7. `customConfirm` Override
Certify uses `customConfirm()` instead of `window.confirm()` for delete/submit dialogs. Override both:
```javascript
window.customConfirm = function() { return true; }
window.confirm = function() { return true; }
```

### 8. Disconnect, Don't Close
Always use `pw.stop()`, never `browser.close()` — closing kills the shared Chrome instance.

### 9. Session Cookies
Certify sessions can persist in the Chrome profile. Check if already logged in before running full login flow.

### 10. Date Conventions
Expense dates follow billing patterns:
- Cellphone bills: 5th of the month
- Internet bills: 17th of the month
- Adjust in config `line_items` as needed

## Expense Pattern Reference

Default pattern (matches typical cellphone + internet reimbursement):

| Item | Amount | Day | Category |
|------|--------|-----|----------|
| Cellphone | $100.00 | 5th | Cellphone & Internet |
| Internet | $20.00 | 17th | Cellphone & Internet |
| **Monthly Total** | **$120.00** | | |

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Amount field not found | Category postback not complete | Add longer delay after category select |
| Vendor not saving | Autocomplete suggestion not clicked | Ensure suggestion div is clicked, not just typed |
| "No Receipt Selected" warning | Receipt not attached from wallet | Upload to wallet first, then attach |
| Upload button disappears | ASP.NET postback hid it | Force-show via JS evaluation |
| Report won't submit | Missing required fields or receipts | Check all expenses have receipts and required fields |
| Login redirect loop | Expired session | Re-run certify_login.py with MFA |

## Monthly Automation

Recommended monthly workflow:

1. Download AT&T invoices (use `att-invoices` skill)
2. Run `certify_login.py` (requires human MFA code)
3. Run `certify_expenses.py full --receipts ~/invoices/att/pdfs/ATTBill_*.pdf --months-back 1`
4. Review in Certify UI
5. Submit with `--confirm` (or submit manually)

The MFA requirement means this always needs human interaction for the verification code.
