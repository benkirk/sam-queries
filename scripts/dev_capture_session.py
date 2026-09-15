#!/usr/bin/env python3
# dev_capture_session.py -- capture one real OIDC session for the load driver.
#
# Opens a headed Chromium at <base>/auth/login; you complete the Entra login
# and 2FA in that window. Once the browser lands back on the app with a session
# cookie, it writes a Playwright storage_state.json that dev_session_load.py (and
# e2e/ via SAM_E2E_STORAGE_STATE) replay. Log in ONCE -- the login path is rate
# limited on dev by design.
#
# The saved file is a credential: it is written outside the repo tree (the
# script refuses an in-repo path) and must never be committed.
#
# Usage:
#   dev_capture_session.py [--base URL] [--out FILE] [--timeout S]
#
# Exit codes: 0 saved / 2 precondition failed.

import argparse
import os
import sys
import tempfile
import time
from urllib.parse import urlsplit

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sys.exit('playwright not found; run: pip install -e ".[e2e]" && playwright install chromium')

DEFAULT_BASE = "https://samuel-dev.k8s.ucar.edu"


def repo_root():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(here)  # scripts/ -> repo root


def main():
    p = argparse.ArgumentParser(description="Capture a real OIDC session cookie for the load driver.")
    p.add_argument("--base", default=os.getenv("SAM_API_BASE", DEFAULT_BASE))
    p.add_argument("--out", default=os.getenv("SAM_E2E_STORAGE_STATE"),
                   help="where to write storage_state.json (default: a temp file)")
    p.add_argument("--timeout", type=float, default=300.0, help="seconds to wait for login")
    args = p.parse_args()

    base = args.base.rstrip("/")
    host = urlsplit(base).netloc
    out = args.out or os.path.join(tempfile.gettempdir(), "sam_dev_session.json")
    out = os.path.abspath(out)
    if out.startswith(repo_root() + os.sep):
        sys.exit(f"refusing to write a session cookie inside the repo: {out}")

    print(f"Opening {base}/auth/login -- log in and complete 2FA in the browser window.")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        ctx = browser.new_context(base_url=base)
        page = ctx.new_page()
        page.goto(base + "/auth/login")
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            url = page.url
            on_app = urlsplit(url).netloc == host and "/auth/login" not in url
            has_session = any(c["name"] == "session" for c in ctx.cookies())
            if on_app and has_session:
                break
            time.sleep(1.0)
        else:
            browser.close()
            sys.exit("timed out waiting for a completed login (no session cookie)")
        ctx.storage_state(path=out)
        browser.close()

    print(f"saved session to {out}")
    print(f"run:  export SAM_E2E_STORAGE_STATE={out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
