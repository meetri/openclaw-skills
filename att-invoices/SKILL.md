---
name: att-invoices
description: Download AT&T invoice PDFs via persistent Chrome + Playwright CDP automation. Use when the user asks to download AT&T bills, invoices, or statements.
---

# AT&T Invoice Downloader

Downloads all available AT&T bill PDFs (typically 12 months) via headless-on-Xvfb Chrome automation with Playwright CDP.

## Architecture

AT&T uses Akamai bot detection. Headless Chrome is blocked. The solution:

```
Xvfb (virtual display :99)
  └─ Real Chrome (non-headless, --remote-debugging-port=9222)
       └─ Playwright connects via CDP
            └─ playwright-stealth patches applied
```

Chrome runs persistently in a tmux session. Scripts connect/disconnect via CDP without killing Chrome. This lets us survive across multiple script runs and agent turns.

## Prerequisites

### Software
- `google-chrome-stable` (real Chrome, not Chromium)
- `xvfb` (virtual framebuffer)
- `tmux`
- Python packages: `playwright`, `playwright-stealth`
- `pass` (password store) with GPG key

### Credentials
AT&T credentials stored in `pass`:
```bash
pass show att/login
# Line 1: password
# user: <email>
# url: https://att.com
```

### Configuration

All settings can be set via config file, env vars, or CLI args (highest priority wins).

**Config file** (`~/.config/att-invoices/config.json`):
```json
{
  "mfa_phone": "XXXX",
  "output_dir": "/home/user/invoices/att",
  "cdp_url": "http://127.0.0.1:9222",
  "mfa_timeout": 300,
  "chrome_profile": "/home/user/.att-chrome-profile"
}
```

**Environment variables**: `ATT_MFA_PHONE`, `ATT_OUTPUT_DIR`, `ATT_CDP_URL`, `ATT_MFA_TIMEOUT`, `ATT_CHROME_PROFILE`

**CLI args**: `--mfa-phone=XXXX`, `--output-dir=...`, `--cdp-url=...`, `--timeout=300`

The `mfa_phone` parameter is the last 4 digits of the phone number to receive the MFA code. If not set, the script selects the first available option.

### MFA
AT&T requires MFA on every login. The agent must ask the human for the verification code sent to the configured phone number.

## Step-by-Step Flow

### Phase 1: Infrastructure Setup (one-time)

```bash
# 1. Start Xvfb in tmux
tmux new-session -d -s xvfb
tmux send-keys -t xvfb 'Xvfb :99 -screen 0 1920x1080x24 &' Enter

# 2. Start Chrome in tmux with CDP
tmux new-session -d -s att-chrome
tmux send-keys -t att-chrome 'DISPLAY=:99 setsid google-chrome-stable \
  --remote-debugging-port=9222 \
  --user-data-dir=$HOME/.att-chrome-profile \
  --disable-blink-features=AutomationControlled \
  --no-first-run \
  --no-default-browser-check \
  --disable-dev-shm-usage \
  --disable-gpu \
  --no-sandbox \
  --window-size=1920,1080 \
  about:blank &' Enter
```

Verify Chrome is running:
```bash
curl -s http://127.0.0.1:9222/json/version | head -5
```

### Phase 2: Login + MFA

Run `scripts/att_login.py`:
```bash
python3 /path/to/skill/scripts/att_login.py
```

This script:
1. Connects to Chrome via CDP on port 9222
2. Navigates to `https://www.att.com/acctmgmt/signin`
3. Fills username (with human-like typing delays)
4. Clicks Continue, waits for password field
5. Fills password, clicks Sign In
6. On MFA page: selects the configured phone number (via `mfa_phone`), clicks Send
7. **Writes status to `~/invoices/att/status.txt`** — agent should tell human "MFA code sent to your phone"
8. Polls `~/invoices/att/mfa_code.txt` for the code (human or agent writes it there)
9. Enters code, submits, waits for account overview page

**Critical: The agent must ask the human for the MFA code and write it to `~/invoices/att/mfa_code.txt`.**

### Phase 3: Navigate to Billing

From the account overview page (`/acctmgmt/overview`):

1. Click the billing link **from within the page** — use `a[href="/acctmgmt/billing/mybillingcenter"]`
2. **DO NOT** navigate directly to the billing URL — AT&T's SPA routing will redirect to the homepage
3. Wait for billing page to load

### Phase 4: Download All PDFs (All Accounts)

Run `scripts/att_download_bills.py`:
```bash
python3 /path/to/skill/scripts/att_download_bills.py
```

This script automatically:
1. Connects to Chrome via CDP
2. Navigates to account overview
3. **Discovers all accounts** (wireless, internet, etc.) from the account switcher tiles
4. For each account:
   - Switches to that account
   - Navigates to billing → "See all statements"
   - Downloads all bill PDFs across all paginated pages
   - Detects pagination loops and stops gracefully
5. Prints a summary of all downloaded bills by account

Bill history pages have `.titleBill` expandable rows. Each row expands to show "Download PDF" → "Regular PDF" dropdown.

### Phase 5: Cleanup

```bash
# Disconnect gracefully (don't kill Chrome)
# Scripts use pw.stop() NOT browser.close()
```

## Output

PDFs saved to `~/invoices/att/pdfs/` with AT&T's naming: `ATTBill_NNNN_MonYYYY.pdf`

Example:
```
ATTBill_NNNN_Feb2026.pdf
ATTBill_NNNN_Jan2026.pdf
ATTBill_NNNN_Dec2025.pdf
...
ATTBill_NNNN_Mar2025.pdf
```

## Critical Lessons (Hard-Won)

1. **`--headless=new` causes 902 errors** — Akamai detects headless. Use real Chrome on Xvfb instead.
2. **`errorCode=920` is MFA, NOT an error** — Don't check for "error" in URL, it false-positives on MFA URLs.
3. **Smart banner modal blocks clicks** — Dismiss with `button:has-text("×")` + `force=True`.
4. **Username gets pre-filled in persistent profiles** — Handle hidden `input[id="userID"]` field.
5. **`browser.close()` kills Chrome via CDP** — Always use `pw.stop()` to disconnect without killing.
6. **`setsid` required for Chrome** — Without it, exec tool kills child processes on timeout.
7. **Session cookies expire immediately across restarts** — Login→MFA→billing must happen in ONE session.
8. **Direct URL navigation fails** — AT&T SPA routing bounces direct `/acctmgmt/billing/...` URLs to homepage. Must click links from within the authenticated app.
9. **Rate limiting** — AT&T returns error 902 after ~3-4 login attempts in 30 minutes. Cool down between retries.
10. **Human-like delays matter** — Use `random.uniform()` for typing delays and pauses. Akamai tracks behavioral patterns.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Error 902 on login | Rate limited or headless detected | Wait 30 min; verify Chrome is non-headless on Xvfb |
| Redirect to homepage | Direct URL navigation | Navigate via in-page link clicks only |
| "It's not you" page | Bot detection triggered | Check stealth patches; use fresh profile |
| MFA page but configured phone not found | Wrong `mfa_phone` value or different MFA flow | Check available options via screenshot; update config |
| Download hangs | Modal blocking or dropdown not open | Dismiss modals first; ensure "Download PDF" dropdown is visible |
| Chrome not on port 9222 | Chrome died | Relaunch in tmux with CDP flags |

## Monthly Automation

To automate monthly, set up a cron job that:
1. Checks if Chrome + Xvfb are running (relaunch if not)
2. Runs the login script
3. Notifies the human for MFA code (unavoidable — AT&T requires it every time)
4. After MFA, runs the download script
5. Only downloads the newest bill (check existing files in pdfs/ dir)

The MFA requirement means this can never be fully unattended — it always needs human interaction for the verification code.
