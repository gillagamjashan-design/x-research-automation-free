#!/usr/bin/env python3
"""Convert Chrome DevTools cookies JSON to Twikit-compatible format."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def convert_chrome_cookies(chrome_path: str, twikit_path: str) -> None:
    with Path(chrome_path).open("r", encoding="utf-8") as cookie_file:
        cookies_raw = json.load(cookie_file)

    if not isinstance(cookies_raw, list):
        print("Error: Chrome cookies should be a list of cookie objects")
        sys.exit(1)

    cookies: dict[str, str] = {}
    for cookie in cookies_raw:
        if _is_cookie_object(cookie):
            cookies[str(cookie["name"])] = str(cookie["value"])

    if not cookies:
        print("Error: no name/value cookie pairs found")
        sys.exit(1)

    with Path(twikit_path).open("w", encoding="utf-8") as cookie_file:
        json.dump(cookies, cookie_file, indent=2)
        cookie_file.write("\n")

    print(f"Converted {len(cookies)} cookies")
    print(f"Saved to {twikit_path}")
    print(f"\nNow use: python3 x_automation_free.py 'query' --cookies {twikit_path}")


def _is_cookie_object(cookie: Any) -> bool:
    return (
        isinstance(cookie, dict)
        and "name" in cookie
        and "value" in cookie
        and bool(cookie["name"])
        and cookie["value"] is not None
    )


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python3 convert_cookies.py chrome_cookies.json twikit_cookies.json")
        sys.exit(1)
    convert_chrome_cookies(sys.argv[1], sys.argv[2])
