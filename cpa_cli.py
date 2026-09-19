#!/usr/bin/env python3
"""CLI for minting xAI OAuth credentials via browser-based device authorization.

Usage:
    python cpa_cli.py --email user@example.com --password secret --auth-dir ./cpa_auths
    python cpa_cli.py --email user@example.com --password secret --headless --proxy socks5://127.0.0.1:1080

Can also be invoked via Hermes: hermes cpa-mint --email ... --password ...
"""
from __future__ import annotations

import argparse
import json
import os
import sys


def _add_sys_path():
    """Ensure the project root is importable."""
    project_root = os.path.dirname(os.path.abspath(__file__))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)


_add_sys_path()


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="cpa-mint",
        description="Mint xAI OAuth credentials via browser device-authorization flow.",
    )
    parser.add_argument("--email", required=True, help="xAI account email")
    parser.add_argument("--password", required=True, help="xAI account password")
    parser.add_argument(
        "--auth-dir",
        default="./cpa_auths",
        help="Directory to write credential JSON files (default: ./cpa_auths)",
    )
    parser.add_argument("--proxy", default="", help="HTTP/SOCKS5 proxy URL")
    parser.add_argument(
        "--headless",
        action="store_true",
        default=False,
        help="Run browser in headless mode (may trigger Cloudflare blocks)",
    )
    parser.add_argument(
        "--base-url",
        default="https://cli-chat-proxy.grok.com/v1",
        help="CPA base URL for the credential file",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=240.0,
        help="Browser authorization timeout in seconds (default: 240)",
    )
    parser.add_argument(
        "--request-timeout",
        type=float,
        default=15.0,
        help="OAuth HTTP request timeout in seconds (default: 15)",
    )
    parser.add_argument(
        "--poll-timeout",
        type=float,
        default=15.0,
        help="Token poll HTTP timeout in seconds (default: 15)",
    )
    parser.add_argument(
        "--no-standalone",
        action="store_true",
        default=False,
        help="Reuse existing browser page instead of launching standalone",
    )
    parser.add_argument(
        "--no-reuse-browser",
        action="store_true",
        default=False,
        help="Do not reuse browser across calls",
    )
    parser.add_argument(
        "--recycle-every",
        type=int,
        default=15,
        help="Recycle browser every N mint attempts (default: 15)",
    )
    parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        default=False,
        help="Output result as JSON",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        default=False,
        help="Print verbose logs to stderr",
    )

    args = parser.parse_args(argv)

    from cpa_xai.mint import mint_and_export

    def log(msg):
        if args.verbose:
            print(msg, file=sys.stderr)

    result = mint_and_export(
        email=args.email,
        password=args.password,
        auth_dir=args.auth_dir,
        proxy=args.proxy or None,
        headless=args.headless,
        base_url=args.base_url,
        browser_timeout_sec=args.timeout,
        force_standalone=not args.no_standalone,
        reuse_browser=not args.no_reuse_browser,
        recycle_every=args.recycle_every,
        log=log,
        request_timeout_sec=args.request_timeout,
        poll_timeout_sec=args.poll_timeout,
    )

    if args.json_output:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif result.get("ok"):
        print("SUCCESS")
        print("  email:    %s" % result.get("email", ""))
        print("  path:     %s" % result.get("path", ""))
        print("  user_code: %s" % result.get("user_code", ""))
        print("  base_url: %s" % result.get("base_url", ""))
    else:
        print("FAILED: %s" % result.get("error", "unknown error"), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
