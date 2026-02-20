#!/usr/bin/env python3
"""
AT&T Login + MFA via persistent Chrome CDP.

Flow: att.com/acctmgmt/signin → username → password → MFA → account overview

MFA code delivery:
  - Agent asks human for the code sent to their phone
  - Code is written to ~/invoices/att/mfa_code.txt
  - This script polls that file and submits the code

Status updates written to ~/invoices/att/status.txt

Usage:
  python3 att_login.py              # Full login + MFA
  python3 att_login.py --skip-mfa   # Login only (if session still active)
"""
import os, sys, time, random, subprocess
from pathlib import Path

# Lazy imports to fail fast on missing deps
try:
    from playwright.sync_api import sync_playwright
    from playwright_stealth import Stealth
except ImportError as e:
    print(f"Missing dependency: {e}")
    print("Install: pip install playwright playwright-stealth")
    sys.exit(1)

CONFIG_FILE = Path(os.environ.get("ATT_CONFIG", Path.home() / ".config" / "att-invoices" / "config.json"))


def load_config():
    """Load config from file, env vars, and CLI args (in priority order)."""
    # Defaults
    cfg = {
        "cdp_url": "http://127.0.0.1:9222",
        "output_dir": str(Path.home() / "invoices" / "att"),
        "mfa_phone": "",
        "mfa_timeout": 300,
        "chrome_profile": str(Path.home() / ".att-chrome-profile"),
    }
    # Config file
    if CONFIG_FILE.exists():
        import json
        cfg.update(json.loads(CONFIG_FILE.read_text()))
    # Env vars override
    env_map = {
        "ATT_CDP_URL": "cdp_url",
        "ATT_OUTPUT_DIR": "output_dir",
        "ATT_MFA_PHONE": "mfa_phone",
        "ATT_MFA_TIMEOUT": "mfa_timeout",
        "ATT_CHROME_PROFILE": "chrome_profile",
    }
    for env_key, cfg_key in env_map.items():
        val = os.environ.get(env_key)
        if val:
            cfg[cfg_key] = int(val) if cfg_key == "mfa_timeout" else val
    # CLI args override (--mfa-phone=XXXX, --timeout=N, etc.)
    for arg in sys.argv[1:]:
        if arg.startswith("--mfa-phone="):
            cfg["mfa_phone"] = arg.split("=", 1)[1]
        elif arg.startswith("--timeout="):
            cfg["mfa_timeout"] = int(arg.split("=", 1)[1])
        elif arg.startswith("--output-dir="):
            cfg["output_dir"] = arg.split("=", 1)[1]
        elif arg.startswith("--cdp-url="):
            cfg["cdp_url"] = arg.split("=", 1)[1]
    return cfg


_cfg = load_config()
CDP_URL = _cfg["cdp_url"]
OUTPUT_DIR = Path(_cfg["output_dir"])
CODE_FILE = OUTPUT_DIR / "mfa_code.txt"
STATUS_FILE = OUTPUT_DIR / "status.txt"
MFA_PHONE_HINT = _cfg["mfa_phone"]
MFA_TIMEOUT = int(_cfg["mfa_timeout"])

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def status(msg):
    """Write status for other processes/agents to read."""
    STATUS_FILE.write_text(msg)
    print(f"[STATUS] {msg}", flush=True)


def get_credentials():
    """Read AT&T credentials from pass store."""
    try:
        result = subprocess.run(["pass", "show", "att/login"],
                                capture_output=True, text=True, check=True)
        lines = result.stdout.strip().split("\n")
        password = lines[0]
        username = None
        for line in lines[1:]:
            if line.startswith("user:"):
                username = line.split(":", 1)[1].strip()
        if not username or not password:
            raise ValueError("Missing user or password in pass att/login")
        return username, password
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("ERROR: Could not read credentials from 'pass att/login'")
        print("Setup: pass insert att/login  (password on line 1, 'user: email' on line 2)")
        sys.exit(1)


def dismiss_modals(page):
    """Dismiss AT&T promotional modals and banners."""
    for sel in ['button:has-text("×")', 'button.close', '[aria-label="Close"]',
                'button:has-text("No thanks")', 'button:has-text("OK")',
                'button:has-text("Accept")']:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click(force=True)
                time.sleep(0.5)
        except Exception:
            pass


def human_delay(low=0.3, high=1.5):
    time.sleep(random.uniform(low, high))


def human_mouse(page, n=3):
    """Random mouse movements to appear human."""
    for _ in range(n):
        page.mouse.move(random.randint(100, 1800), random.randint(100, 900))
        time.sleep(random.uniform(0.15, 0.4))


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
        # Navigate to login
        status("NAVIGATING_TO_LOGIN")
        page.goto("https://www.att.com/acctmgmt/signin", wait_until="domcontentloaded")
        time.sleep(random.uniform(6, 9))
        dismiss_modals(page)
        human_mouse(page)

        page.screenshot(path=str(OUTPUT_DIR / "step_login.png"))
        print(f"  URL: {page.url}", flush=True)

        # Check for block
        body = (page.text_content("body") or "").lower()
        if "it's not you" in body:
            status("ERROR_BLOCKED")
            page.screenshot(path=str(OUTPUT_DIR / "error_blocked.png"))
            return False

        # Fill username
        status("ENTERING_USERNAME")
        uid_field = page.query_selector('input[id="userID"]:not([type="hidden"])')
        pw_field = page.query_selector('input[type="password"]')

        if uid_field and uid_field.is_visible():
            uid_field.click(force=True)
            human_delay()
            page.type('input[id="userID"]', username, delay=random.randint(70, 120))
            human_delay(1, 2)
            page.click('button:has-text("Continue")')
            status("USERNAME_SUBMITTED")
            time.sleep(random.uniform(5, 8))
            pw_field = page.wait_for_selector('input[type="password"]', timeout=20000)
        elif pw_field:
            status("USERNAME_PREFILLED")
        else:
            pw_field = page.wait_for_selector('input[type="password"]', timeout=20000)

        # Fill password
        status("ENTERING_PASSWORD")
        pw_field.click(force=True)
        human_delay()
        page.type('input[type="password"]', password, delay=random.randint(50, 100))
        human_delay(0.5, 1.5)
        page.click('button:has-text("Sign in")')
        status("SIGN_IN_CLICKED")
        time.sleep(random.uniform(10, 14))

        # Check for rate limiting
        if "errorCode=902" in page.url:
            status("ERROR_RATE_LIMITED_902")
            page.screenshot(path=str(OUTPUT_DIR / "error_902.png"))
            print("Rate limited by AT&T. Wait 30+ minutes before retrying.", flush=True)
            return False

        body = (page.text_content("body") or "").lower()
        if "it's not you" in body:
            status("ERROR_BLOCKED")
            return False

        # Check if logged in without MFA
        if "acctmgmt" in page.url and "signin" not in page.url:
            status("LOGGED_IN")
            page.screenshot(path=str(OUTPUT_DIR / "step_loggedin.png"))
            print(f"  Logged in (no MFA): {page.url}", flush=True)
            return True

        if skip_mfa:
            status("MFA_REQUIRED_BUT_SKIPPED")
            return False

        # MFA flow
        status("MFA_PAGE")
        time.sleep(5)
        page.wait_for_load_state("domcontentloaded")
        page.screenshot(path=str(OUTPUT_DIR / "step_mfa.png"))

        # Select phone number
        if MFA_PHONE_HINT:
            phone_opt = page.query_selector(f'text={MFA_PHONE_HINT}')
            if not phone_opt:
                status("ERROR_NO_MFA_PHONE")
                page.screenshot(path=str(OUTPUT_DIR / "error_no_mfa_phone.png"))
                print(f"Could not find MFA option with '{MFA_PHONE_HINT}'", flush=True)
                print("Set mfa_phone in config or pass --mfa-phone=XXXX", flush=True)
                return False
        else:
            # No phone hint configured — click the first radio/option
            phone_opt = page.query_selector('input[type="radio"]')
            if not phone_opt:
                status("ERROR_NO_MFA_OPTIONS")
                page.screenshot(path=str(OUTPUT_DIR / "error_no_mfa_options.png"))
                print("No MFA phone options found. Set --mfa-phone=XXXX", flush=True)
                return False
            print("No mfa_phone configured — using first available option", flush=True)

        phone_opt.click()
        time.sleep(1)
        send_btn = page.query_selector('button:has-text("Send")')
        if not send_btn:
            status("ERROR_NO_SEND_BUTTON")
            return False

        send_btn.click()
        status("MFA_CODE_SENT")
        time.sleep(5)
        page.screenshot(path=str(OUTPUT_DIR / "step_mfa_sent.png"))
        print(f"  MFA code sent to phone ending in {MFA_PHONE_HINT}", flush=True)
        print(f"  Waiting for code in {CODE_FILE} (timeout: {MFA_TIMEOUT}s)...", flush=True)

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
                      page.query_selector('input[placeholder*="code"]'))
        if not code_input:
            for inp in page.query_selector_all('input'):
                if inp.is_visible() and page.evaluate("e => e.type", inp) in ("tel", "text", "number"):
                    code_input = inp
                    break

        if not code_input:
            status("ERROR_NO_CODE_INPUT")
            page.screenshot(path=str(OUTPUT_DIR / "error_no_code_input.png"))
            return False

        code_input.click(force=True)
        human_delay(0.2, 0.5)
        code_input.type(code, delay=random.randint(70, 110))
        time.sleep(1)

        # Submit
        for sel in ['button:has-text("Continue")', 'button:has-text("Verify")', 'button:has-text("Submit")']:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                btn.click(force=True)
                break

        status("MFA_SUBMITTED")
        try:
            page.wait_for_url("**/acctmgmt/**", timeout=30000)
        except Exception:
            pass
        time.sleep(5)

        if "acctmgmt" in page.url and "signin" not in page.url:
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
        # IMPORTANT: pw.stop() NOT browser.close() — don't kill Chrome!
        pw.stop()


if __name__ == "__main__":
    skip = "--skip-mfa" in sys.argv
    success = login(skip_mfa=skip)
    sys.exit(0 if success else 1)
