#!/usr/bin/env python3
"""
Certify/Emburse Login + MFA via persistent Chrome CDP.

Flow: expense.certify.com → email → password → MFA (SMS) → dashboard

MFA code delivery:
  - Agent asks human for the code sent to their phone
  - Code is written to ~/expenses/certify/mfa_code.txt
  - This script polls that file and submits the code

Status updates written to ~/expenses/certify/status.txt

Usage:
  python3 certify_login.py
  python3 certify_login.py --skip-mfa   # If session still active
"""
import os, sys, time, random, subprocess, json
from pathlib import Path

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


def load_config():
    """Load config from file → env vars → CLI args (priority order)."""
    cfg = {
        "cdp_url": "http://127.0.0.1:9222",
        "output_dir": str(Path.home() / "expenses" / "certify"),
        "mfa_phone": "",       # last 4 digits of MFA phone
        "mfa_timeout": 300,
        "chrome_profile": str(Path.home() / ".certify-chrome-profile"),
        "pass_path": "certify/login",  # path in `pass` store
    }
    if CONFIG_FILE.exists():
        cfg.update(json.loads(CONFIG_FILE.read_text()))
    env_map = {
        "CERTIFY_CDP_URL": "cdp_url",
        "CERTIFY_OUTPUT_DIR": "output_dir",
        "CERTIFY_MFA_PHONE": "mfa_phone",
        "CERTIFY_MFA_TIMEOUT": "mfa_timeout",
        "CERTIFY_CHROME_PROFILE": "chrome_profile",
        "CERTIFY_PASS_PATH": "pass_path",
    }
    for env_key, cfg_key in env_map.items():
        val = os.environ.get(env_key)
        if val:
            cfg[cfg_key] = int(val) if cfg_key == "mfa_timeout" else val
    for arg in sys.argv[1:]:
        if arg.startswith("--mfa-phone="):
            cfg["mfa_phone"] = arg.split("=", 1)[1]
        elif arg.startswith("--timeout="):
            cfg["mfa_timeout"] = int(arg.split("=", 1)[1])
        elif arg.startswith("--output-dir="):
            cfg["output_dir"] = arg.split("=", 1)[1]
        elif arg.startswith("--cdp-url="):
            cfg["cdp_url"] = arg.split("=", 1)[1]
        elif arg.startswith("--pass-path="):
            cfg["pass_path"] = arg.split("=", 1)[1]
    return cfg


_cfg = load_config()
CDP_URL = _cfg["cdp_url"]
OUTPUT_DIR = Path(_cfg["output_dir"])
CODE_FILE = OUTPUT_DIR / "mfa_code.txt"
STATUS_FILE = OUTPUT_DIR / "status.txt"
MFA_PHONE_HINT = _cfg["mfa_phone"]
MFA_TIMEOUT = int(_cfg["mfa_timeout"])
PASS_PATH = _cfg["pass_path"]

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def status(msg):
    STATUS_FILE.write_text(msg)
    print(f"[STATUS] {msg}", flush=True)


def get_credentials():
    """Read Certify credentials from pass store."""
    try:
        result = subprocess.run(["pass", "show", PASS_PATH],
                                capture_output=True, text=True, check=True)
        lines = result.stdout.strip().split("\n")
        password = lines[0]
        username = None
        for line in lines[1:]:
            if line.startswith("user:"):
                username = line.split(":", 1)[1].strip()
        if not username or not password:
            raise ValueError(f"Missing user or password in pass {PASS_PATH}")
        return username, password
    except (subprocess.CalledProcessError, FileNotFoundError):
        print(f"ERROR: Could not read credentials from 'pass {PASS_PATH}'")
        print(f"Setup: pass insert {PASS_PATH}  (password on line 1, 'user: email' on line 2)")
        sys.exit(1)


def human_delay(low=0.3, high=1.5):
    time.sleep(random.uniform(low, high))


def login(skip_mfa=False):
    username, password = get_credentials()
    CODE_FILE.unlink(missing_ok=True)

    pw = sync_playwright().start()
    try:
        browser = pw.chromium.connect_over_cdp(CDP_URL)
    except Exception as e:
        print(f"ERROR: Cannot connect to Chrome CDP at {CDP_URL}")
        print(f"  {e}")
        print("Run setup_chrome.sh first.")
        pw.stop()
        sys.exit(1)

    ctx = browser.contexts[0]
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    Stealth().apply_stealth_sync(page)

    try:
        status("NAVIGATING_TO_LOGIN")
        page.goto("https://expense.certify.com", wait_until="domcontentloaded")
        time.sleep(random.uniform(3, 5))
        page.screenshot(path=str(OUTPUT_DIR / "step_login.png"))

        # Check if already logged in
        if "Default.aspx" in page.url or "ExpRptList" in page.url:
            status("ALREADY_LOGGED_IN")
            print(f"  Already logged in: {page.url}", flush=True)
            return True

        # Fill email
        status("ENTERING_EMAIL")
        email_input = page.wait_for_selector('input[type="email"], input[name*="Email"], #txtEmail', timeout=15000)
        email_input.click()
        human_delay()
        email_input.fill("")
        email_input.type(username, delay=random.randint(50, 100))
        human_delay(0.5, 1.0)

        # Click Next/Continue
        next_btn = page.query_selector('input[type="submit"], button:has-text("Next"), button:has-text("Continue"), #btnNext')
        if next_btn:
            next_btn.click()
            time.sleep(random.uniform(3, 5))

        # Fill password
        status("ENTERING_PASSWORD")
        pw_input = page.wait_for_selector('input[type="password"]', timeout=15000)
        pw_input.click()
        human_delay()
        pw_input.type(password, delay=random.randint(50, 100))
        human_delay(0.5, 1.0)

        # Click Sign In
        sign_in = page.query_selector('input[type="submit"], button:has-text("Sign In"), button:has-text("Log In"), #btnLogin')
        if sign_in:
            sign_in.click()

        status("SIGN_IN_CLICKED")
        time.sleep(random.uniform(5, 8))
        page.screenshot(path=str(OUTPUT_DIR / "step_after_signin.png"))

        # Check if logged in (no MFA)
        if any(x in page.url for x in ["Default.aspx", "ExpRptList", "dashboard"]):
            status("LOGGED_IN")
            print(f"  Logged in (no MFA): {page.url}", flush=True)
            return True

        if skip_mfa:
            status("MFA_REQUIRED_BUT_SKIPPED")
            return False

        # MFA flow — Certify uses SMS verification
        status("MFA_PAGE")
        page.screenshot(path=str(OUTPUT_DIR / "step_mfa.png"))

        # Select phone if hint provided
        if MFA_PHONE_HINT:
            phone_opt = page.query_selector(f'text=*{MFA_PHONE_HINT}')
            if phone_opt:
                phone_opt.click()
                time.sleep(1)

        # Click Send / Text Me
        for sel in ['button:has-text("Send")', 'button:has-text("Text")',
                    'a:has-text("Text me")', 'input[value*="Send"]',
                    'button:has-text("Verify")', '#btnSendCode']:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                btn.click()
                break

        status("MFA_CODE_SENT")
        time.sleep(3)
        page.screenshot(path=str(OUTPUT_DIR / "step_mfa_sent.png"))
        print(f"  MFA code sent. Waiting for code in {CODE_FILE} (timeout: {MFA_TIMEOUT}s)...", flush=True)

        # Poll for code
        start = time.time()
        code = None
        while time.time() - start < MFA_TIMEOUT:
            if CODE_FILE.exists():
                code = CODE_FILE.read_text().strip()
                if code:
                    break
            time.sleep(2)

        if not code:
            status("ERROR_MFA_TIMEOUT")
            print("MFA code not received in time.", flush=True)
            return False

        # Enter code
        status("ENTERING_MFA_CODE")
        code_input = (page.query_selector('input[type="tel"]') or
                      page.query_selector('input[name*="code" i]') or
                      page.query_selector('input[name*="Code"]') or
                      page.query_selector('input[id*="Code"]'))
        if not code_input:
            for inp in page.query_selector_all('input'):
                if inp.is_visible() and page.evaluate("e => e.type", inp) in ("tel", "text", "number"):
                    code_input = inp
                    break

        if not code_input:
            status("ERROR_NO_CODE_INPUT")
            page.screenshot(path=str(OUTPUT_DIR / "error_no_code_input.png"))
            return False

        code_input.click()
        human_delay(0.2, 0.5)
        code_input.type(code, delay=random.randint(50, 90))
        time.sleep(1)

        # Submit MFA
        for sel in ['button:has-text("Verify")', 'button:has-text("Continue")',
                    'button:has-text("Submit")', 'input[type="submit"]', '#btnVerify']:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                btn.click()
                break

        status("MFA_SUBMITTED")
        time.sleep(random.uniform(5, 10))

        if any(x in page.url for x in ["Default.aspx", "ExpRptList", "dashboard"]):
            status("LOGGED_IN")
            page.screenshot(path=str(OUTPUT_DIR / "step_loggedin.png"))
            print(f"  Successfully logged in: {page.url}", flush=True)
            return True
        else:
            status("ERROR_LOGIN_FAILED")
            page.screenshot(path=str(OUTPUT_DIR / "error_login_failed.png"))
            print(f"  Login may have failed. URL: {page.url}", flush=True)
            return False

    except Exception as e:
        status(f"ERROR_{type(e).__name__}")
        print(f"Exception: {e}", flush=True)
        try:
            page.screenshot(path=str(OUTPUT_DIR / "error_exception.png"))
        except Exception:
            pass
        return False
    finally:
        pw.stop()  # Disconnect, don't kill Chrome


if __name__ == "__main__":
    skip = "--skip-mfa" in sys.argv
    success = login(skip_mfa=skip)
    sys.exit(0 if success else 1)
