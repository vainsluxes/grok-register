"""Unified headless CLI pipeline for account registration and OAuth minting.

Subcommands:
  register  – Register N new xAI accounts via temp-mail + browser automation.
  mint      – Mint OAuth device-flow credentials for unminted accounts.
  audit     – Inspect accounts, minted credentials, and 9Router DB status.
"""

import argparse
import glob
import json
import logging
import os
import signal
import sqlite3
import sys
import time
from pathlib import Path

# Ensure project root is importable
_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

DB_PATH = os.environ.get("NINE_ROUTER_DB") or os.path.expanduser(
    "~/.9router/db/data.sqlite"
)

logger = logging.getLogger("grok_pipeline")

# ── helpers ──────────────────────────────────────────────────────────────────


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "[%(asctime)s] %(levelname)-7s %(message)s"
    logging.basicConfig(level=level, format=fmt, datefmt="%H:%M:%S")
    # Force the level even if basicConfig was already called
    logging.getLogger().setLevel(level)


def _log_line(message: str) -> None:
    """Callback-compatible logging sink used by registration/mint modules."""
    logger.info(message)


def _graceful_exit(signum, frame):
    logger.warning("Interrupted (signal %s), exiting.", signum)
    sys.exit(130)


def _parse_accounts_file(path: str) -> list:
    """Parse accounts.txt -> list of {email, password, sso}."""
    accounts = []
    if not os.path.isfile(path):
        return accounts
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            raw = line.strip()
            if not raw:
                continue
            # Strip optional leading index: "12|email----pass----..."
            if "|" in raw.split("----", 1)[0]:
                raw = raw.split("|", 1)[1]
            parts = raw.split("----")
            if len(parts) >= 2 and parts[0] and parts[1]:
                email = parts[0].strip()
                password = parts[1].strip()
                sso = parts[2].strip() if len(parts) >= 3 else ""
                if "@" in email:
                    accounts.append(
                        {"email": email, "password": password, "sso": sso}
                    )
    return accounts


def _find_accounts_file() -> str:
    """Locate accounts.txt or latest accounts_*.txt."""
    single = str(_PROJECT_ROOT / "accounts.txt")
    if os.path.isfile(single):
        return single
    candidates = glob.glob(str(_PROJECT_ROOT / "accounts_*.txt"))
    if not candidates:
        return ""
    return max(candidates, key=os.path.getmtime)


def _get_minted_emails() -> set:
    """Collect emails already minted (in cpa_auths/ or 9Router grok-cli)."""
    emails: set = set()
    # CPA auth files
    auth_dir = str(_PROJECT_ROOT / "cpa_auths")
    for path in glob.glob(os.path.join(auth_dir, "xai-*.json")):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            email = str(data.get("email") or "").strip().lower()
            if email and "@" in email:
                emails.add(email)
        except Exception:
            base = os.path.basename(path)
            if base.startswith("xai-") and base.endswith(".json"):
                emails.add(base[4:-5].lower())
    # Exclude previously failed accounts
    failed_file = os.path.join(auth_dir, "cpa_auth_failed.txt")
    if os.path.isfile(failed_file):
        try:
            with open(failed_file, "r", encoding="utf-8") as fh:
                for line in fh:
                    parts = line.strip().split("----")
                    if parts and "@" in parts[0]:
                        emails.add(parts[0].strip().lower())
        except Exception:
            pass
    # 9Router DB
    if os.path.isfile(DB_PATH):
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT name, email, data FROM providerConnections WHERE provider='grok-cli'"
            )
            for name, email, data in cursor.fetchall():
                for candidate in (email, name):
                    if candidate and "@" in str(candidate):
                        emails.add(str(candidate).strip().lower())
                try:
                    d = json.loads(data) if isinstance(data, str) else (data or {})
                    ps = d.get("providerSpecificData") or {}
                    e = (ps.get("email") or d.get("email") or "").strip().lower()
                    if e and "@" in e:
                        emails.add(e)
                except Exception:
                    pass
            conn.close()
        except Exception:
            pass
    return emails


def _get_9router_emails() -> set:
    """Get all grok-cli emails from 9Router DB."""
    emails: set = set()
    if not os.path.isfile(DB_PATH):
        return emails
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name, email, data FROM providerConnections WHERE provider='grok-cli'"
        )
        for name, email, data in cursor.fetchall():
            for candidate in (email, name):
                if candidate and "@" in str(candidate):
                    emails.add(str(candidate).strip().lower())
            try:
                d = json.loads(data) if isinstance(data, str) else (data or {})
                ps = d.get("providerSpecificData") or {}
                e = (ps.get("email") or d.get("email") or "").strip().lower()
                if e and "@" in e:
                    emails.add(e)
            except Exception:
                pass
        conn.close()
    except Exception:
        pass
    return emails


def _get_failed_emails() -> set:
    """Get emails from cpa_auths/cpa_auth_failed.txt."""
    failed: set = set()
    fail_file = str(_PROJECT_ROOT / "cpa_auths" / "cpa_auth_failed.txt")
    if not os.path.isfile(fail_file):
        return failed
    try:
        with open(fail_file, "r", encoding="utf-8") as fh:
            for line in fh:
                parts = line.strip().split("----")
                if parts and "@" in parts[0]:
                    failed.add(parts[0].strip().lower())
    except Exception:
        pass
    return failed


# ── REGISTER subcommand ──────────────────────────────────────────────────────


def _prepare_headless_display():
    """Ensure Xvfb / browser_hide_mode is active for headless operation."""
    try:
        from browser_runtime import prepare_browser_display

        mode = prepare_browser_display(log_callback=_log_line)
        logger.info("Display mode: %s", mode)
        return mode
    except Exception as exc:
        logger.warning("prepare_browser_display failed: %s", exc)
        return "normal"


def cmd_register(args):
    """Register N new accounts, optionally mint + push to 9Router."""
    _prepare_headless_display()

    from app_config import load_config
    from registration_flow import (
        BatchResult,
        RegistrationCallbacks,
        RegistrationOperations,
        RegistrationSettings,
        register_one_account,
        persist_account_result,
        run_batch,
    )
    from registration_browser import (
        start_browser,
        restart_browser,
        stop_browser,
        open_signup_page,
        fill_email_and_submit,
        fill_code_and_submit,
        fill_profile_and_submit,
        wait_for_sso_cookie,
        enable_nsfw_for_token,
        cleanup_runtime_memory,
    )
    from browser_runtime import (
        configure_runtime,
        get_configured_proxy,
        get_proxies,
        http_get,
        http_post,
        get_browser_hide_mode,
    )
    from mail_service import (
        get_email_and_token,
        get_oai_code,
    )
    from account_outputs import (
        append_account_line,
        save_mail_credential,
        queue_unsaved_account,
        add_token_to_grok2api_pools,
    )
    from cpa_export import export_cpa_xai_for_account, export_cookies_from_page

    cfg = load_config()
    configure_runtime(cfg)

    # Bind runtime dependencies to modules that need them
    import mail_service as _ms
    import registration_browser as _rb
    import account_outputs as _ao

    runtime_ns = {
        "config": cfg,
        "get_email_and_token": get_email_and_token,
        "get_oai_code": get_oai_code,
        "http_get": http_get,
        "http_post": http_post,
        "get_proxies": get_proxies,
        "get_user_agent": _ms.get_user_agent,
        "get_configured_proxy": get_configured_proxy,
        "prepare_browser_proxy": __import__("browser_runtime").prepare_browser_proxy,
        "create_browser_options": __import__("browser_runtime").create_browser_options,
        "page_has_proxy_error": __import__("browser_runtime").page_has_proxy_error,
        "sleep_with_cancel": lambda sec, cancel=None: time.sleep(sec),
        "raise_if_cancelled": lambda cancel: None,
    }

    _ms.bind_runtime(runtime_ns)
    _rb.bind_runtime(runtime_ns)

    try:
        _ao.configure_token_runtime(
            config_ref=cfg,
            http_get=http_get,
            http_post=http_post,
            log_exception=lambda msg, exc, log_cb=None: str(exc),
        )
    except Exception:
        pass

    # Cancelled sentinel
    class _Cancelled(Exception):
        pass

    class _RetryNeeded(Exception):
        pass

    cancelled_flag = [False]

    def _on_cancel_signal(signum, frame):
        cancelled_flag[0] = True
        logger.warning("Cancellation requested (signal %s).", signum)

    signal.signal(signal.SIGINT, _on_cancel_signal)

    accounts_file = str(_PROJECT_ROOT / "accounts.txt")

    def _persist(email, password, sso):
        append_account_line(accounts_file, email, password, sso)

    # page reference for cookie injection
    _page_ref = [None]

    def _export_cpa(email, password, sso):
        return export_cpa_xai_for_account(
            email=email, password=password,
            page=_page_ref[0], sso=sso,
            config=cfg, log_callback=_log_line,
        )

    callbacks = RegistrationCallbacks(
        log=_log_line,
        cancelled=lambda: cancelled_flag[0],
    )

    ops = RegistrationOperations(
        start_browser=lambda: start_browser(log_callback=_log_line),
        restart_browser=lambda: restart_browser(log_callback=_log_line),
        browser_missing=lambda: _rb.browser is None,
        open_signup_page=lambda: open_signup_page(log_callback=_log_line),
        fill_email_and_submit=lambda: fill_email_and_submit(log_callback=_log_line),
        save_mail_credential=lambda email, token: save_mail_credential(
            str(_PROJECT_ROOT), email, token
        ),
        fill_code_and_submit=lambda email, token: fill_code_and_submit(
            email, token, log_callback=_log_line
        ),
        fill_profile_and_submit=lambda: fill_profile_and_submit(
            log_callback=_log_line
        ),
        wait_for_sso_cookie=lambda: wait_for_sso_cookie(log_callback=_log_line),
        enable_nsfw=lambda sso: enable_nsfw_for_token(sso, log_callback=_log_line),
        persist_account_line=_persist,
        queue_unsaved_result=lambda payload, error: queue_unsaved_account(
            accounts_file, payload, error
        ),
        add_tokens=lambda sso, email: add_token_to_grok2api_pools(
            sso, email=email, log_callback=_log_line
        ),
        export_cpa=_export_cpa,
        cleanup=lambda reason: cleanup_runtime_memory(
            log_callback=_log_line, reason=reason
        ),
        sleep=lambda seconds: time.sleep(seconds),
        cancelled_exception=_Cancelled,
        retry_exception=_RetryNeeded,
    )

    def _observer(result, account, output):
        pass

    logger.info(
        "Starting registration: count=%d, mint=%s, push_9router=%s, sleep=%s",
        args.count, args.mint, args.push_9router, args.sleep,
    )

    batch = run_batch(
        count=args.count,
        callbacks=callbacks,
        observer=_observer,
        ops=ops,
        enable_nsfw=True,
        cleanup_interval=5,
        max_slot_retry=3,
        max_mail_retry=3,
    )

    logger.info(
        "Registration complete: success=%d, fail=%d, processed=%d",
        batch.success_count, batch.fail_count, batch.processed_count,
    )

    # Post-registration mint if requested
    if args.mint and batch.success_count > 0:
        logger.info("Post-registration mint starting for %d accounts...", batch.success_count)
        _do_mint(
            limit=batch.success_count,
            sleep_sec=args.sleep,
            push_9router=args.push_9router,
        )

    return batch


# ── MINT subcommand ──────────────────────────────────────────────────────────


def _do_mint(limit=None, sleep_sec=8.0, push_9router=True):
    """Core mint logic shared by cmd_mint and post-registration mint."""
    from auto_import_grok_cli import (
        parse_accounts,
        mint_oauth,
        insert_to_9router,
        read_cpa_auth,
    )

    _prepare_headless_display()

    accounts_file = _find_accounts_file()
    if not accounts_file:
        logger.error("No accounts file found.")
        return 0, 0

    all_accounts = parse_accounts(accounts_file)
    minted = _get_minted_emails()
    unminted = [
        a for a in all_accounts if a["email"].strip().lower() not in minted
    ]

    if limit is not None:
        unminted = unminted[:limit]

    if not unminted:
        logger.info("No unminted accounts to process.")
        return 0, 0

    logger.info(
        "Mint: %d accounts total, %d already minted, %d to process.",
        len(all_accounts), len(minted), len(unminted),
    )

    ok_count = 0
    fail_count = 0

    for i, acc in enumerate(unminted, 1):
        email = acc["email"]
        logger.info("[%d/%d] Minting: %s", i, len(unminted), email)
        try:
            result = mint_oauth(email, acc["password"], save_auth_file=True)
            if result.get("ok"):
                auth = result if result.get("access_token") else read_cpa_auth(email)
                if push_9router and auth:
                    if insert_to_9router(email, auth):
                        logger.info("[+] %s -> 9Router grok-cli OK", email)
                    else:
                        logger.warning("[!] %s -> 9Router insert failed", email)
                ok_count += 1
                logger.info("[+] Mint success: %s", email)
            else:
                fail_count += 1
                err_msg = str(result.get("error") or "unknown_error")
                logger.warning(
                    "[-] Mint failed: %s: %s", email, err_msg
                )
                try:
                    failed_path = os.path.join(str(_PROJECT_ROOT / "cpa_auths"), "cpa_auth_failed.txt")
                    with open(failed_path, "a", encoding="utf-8") as ff:
                        ff.write(f"{email}----{err_msg}----{int(time.time())}\n")
                except Exception:
                    pass
        except Exception as exc:
            fail_count += 1
            logger.error("[-] Mint error: %s: %s", email, exc)
            try:
                failed_path = os.path.join(str(_PROJECT_ROOT / "cpa_auths"), "cpa_auth_failed.txt")
                with open(failed_path, "a", encoding="utf-8") as ff:
                    ff.write(f"{email}----{str(exc)}----{int(time.time())}\n")
            except Exception:
                pass

        if sleep_sec and i < len(unminted):
            logger.debug("Sleeping %.1fs between mints...", sleep_sec)
            time.sleep(sleep_sec)

    logger.info("Mint complete: ok=%d, fail=%d", ok_count, fail_count)
    return ok_count, fail_count


def cmd_mint(args):
    """Mint OAuth credentials for unminted accounts."""
    return _do_mint(
        limit=args.limit,
        sleep_sec=args.sleep,
        push_9router=True,
    )


# ── AUDIT subcommand ─────────────────────────────────────────────────────────


def cmd_audit(args):
    """Print a structured status summary of the pipeline state."""
    accounts_file = _find_accounts_file()
    all_accounts = _parse_accounts_file(accounts_file) if accounts_file else []
    all_emails = {a["email"].strip().lower() for a in all_accounts}

    minted_emails = _get_minted_emails()
    router_emails = _get_9router_emails()
    failed_emails = _get_failed_emails()

    unminted = all_emails - minted_emails - failed_emails
    minted_in_accounts = all_emails & minted_emails
    in_9router = all_emails & router_emails
    failed_in_accounts = all_emails & failed_emails

    # CPA auth file count
    auth_dir = str(_PROJECT_ROOT / "cpa_auths")
    cpa_files = glob.glob(os.path.join(auth_dir, "xai-*.json"))

    db_exists = os.path.isfile(DB_PATH)
    db_total = 0
    if db_exists:
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM providerConnections WHERE provider='grok-cli'"
            )
            db_total = cursor.fetchone()[0]
            conn.close()
        except Exception:
            pass

    # Output
    border = "=" * 60
    print(border)
    print("  Grok Pipeline Audit Report")
    print(border)
    print()
    print("  Accounts file:          %s" % (accounts_file or "(not found)"))
    print("  Total accounts:         %d" % len(all_accounts))
    print()
    print("  Minted (cpa_auths/):    %d files" % len(cpa_files))
    print("  Minted accounts:        %d" % len(minted_in_accounts))
    print("  Unminted accounts:      %d" % len(unminted))
    print("  Failed accounts:        %d" % len(failed_in_accounts))
    print()
    print("  9Router DB:             %s" % ("OK" if db_exists else "NOT FOUND"))
    print("  9Router DB path:        %s" % DB_PATH)
    print("  9Router grok-cli total: %d" % db_total)
    print("  9Router from accounts:  %d" % len(in_9router))
    print()

    if unminted and len(unminted) <= 20:
        print("  Unminted emails:")
        for email in sorted(unminted):
            print("    - %s" % email)
        print()

    print(border)

    return {
        "accounts_file": accounts_file,
        "total_accounts": len(all_accounts),
        "minted_count": len(minted_in_accounts),
        "unminted_count": len(unminted),
        "failed_count": len(failed_in_accounts),
        "cpa_files": len(cpa_files),
        "db_exists": db_exists,
        "db_grok_cli_total": db_total,
        "db_from_accounts": len(in_9router),
    }


# ── CLI entry point ──────────────────────────────────────────────────────────



def cmd_sync_oracle(args):
    """Vet minted accounts in staging and bulk import them into Oracle 9Router."""
    target_url = (
        args.target_url
        or os.environ.get("ORACLE_9ROUTER_URL")
        or "http://100.113.193.124:20128"
    )
    target_token = (
        args.target_token
        or os.environ.get("ORACLE_9ROUTER_TOKEN")
        or "44a8d9640cde446f"
    )

    auth_dir = _PROJECT_ROOT / "cpa_auths"
    files = sorted(glob.glob(str(auth_dir / "xai-*.json")))
    if not files:
        logger.warning("No minted accounts found in %s", auth_dir)
        return

    accounts_to_push = []
    logger.info("Found %d minted account files in staging (%s)", len(files), auth_dir)

    for p in files:
        if args.limit and len(accounts_to_push) >= args.limit:
            break
        try:
            with open(p, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            email = data.get("email")
            access_token = data.get("access_token")
            refresh_token = data.get("refresh_token")
            if not email or not access_token:
                continue

            if args.probe:
                try:
                    import urllib.request
                    req = urllib.request.Request(
                        "https://cli-chat-proxy.grok.com/v1/models",
                        headers={"Authorization": f"Bearer {access_token}", "User-Agent": "grok-cli/9router"}
                    )
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        if resp.status != 200:
                            logger.warning("[%s] Probe failed (HTTP %s), skipping", email, resp.status)
                            continue
                except Exception as exc:
                    logger.warning("[%s] Probe error (%s), skipping", email, exc)
                    continue

            accounts_to_push.append({
                "email": email,
                "accessToken": access_token,
                "refreshToken": refresh_token,
                "idToken": data.get("id_token", ""),
                "expiresAt": data.get("expired", ""),
                "expiresIn": data.get("expires_in", 21600),
                "displayName": data.get("displayName", "") or email,
                "providerSpecificData": {
                    "authMethod": "device_code",
                    "idToken": data.get("id_token", ""),
                    "email": email,
                    "userId": data.get("sub", ""),
                    "hasGrokCodeAccess": True,
                }
            })
            logger.info("[+] Vetted: %s", email)
        except Exception as exc:
            logger.debug("Error reading %s: %s", p, exc)

    if not accounts_to_push:
        logger.warning("No valid accounts ready to sync.")
        return

    logger.info("Ready to push %d vetted accounts to Oracle 9Router (%s)", len(accounts_to_push), target_url)

    if args.dry_run:
        logger.info("[DRY-RUN] Would push %d accounts. Exiting.", len(accounts_to_push))
        return

    try:
        import urllib.request
        req = urllib.request.Request(
            f"{target_url.rstrip('/')}/api/oauth/grok-cli/bulk-import",
            data=json.dumps({"accounts": accounts_to_push}).encode(),
            headers={
                "Content-Type": "application/json",
                "x-9r-cli-token": str(target_token),
            }
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            res = json.loads(resp.read().decode())
            logger.info("[+] Oracle 9Router response: total=%s success=%s failed=%s",
                        res.get("total"), res.get("success"), res.get("failed"))
    except Exception as exc:
        logger.error("[!] Failed to push to Oracle 9Router: %s", exc)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="grok_pipeline",
        description="Unified headless CLI for Grok account registration, OAuth minting, and auditing.",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable debug logging."
    )
    sub = parser.add_subparsers(dest="command", help="Subcommand to run.")

    # register
    reg = sub.add_parser(
        "register", help="Register N new xAI accounts."
    )
    reg.add_argument(
        "--count", type=int, default=1, help="Number of accounts to register (default: 1)."
    )
    reg.add_argument(
        "--mint",
        action="store_true",
        default=False,
        help="Immediately mint OAuth after registration.",
    )
    reg.add_argument(
        "--push-9router",
        action="store_true",
        default=False,
        help="Push minted credentials into 9Router SQLite.",
    )
    reg.add_argument(
        "--sleep",
        type=float,
        default=6.0,
        help="Delay in seconds between registrations/mints (default: 6).",
    )

    # mint
    mi = sub.add_parser(
        "mint", help="Mint OAuth credentials for unminted accounts."
    )
    mi.add_argument(
        "--limit", type=int, default=None, help="Max accounts to mint."
    )
    mi.add_argument(
        "--sleep",
        type=float,
        default=8.0,
        help="Delay between mints in seconds (default: 8).",
    )

    # audit
    sub.add_parser("audit", help="Print pipeline status summary.")

    # sync-oracle
    sync = sub.add_parser(
        "sync-oracle",
        aliases=["push-oracle"],
        help="Vet staging accounts and bulk import to Oracle 9Router.",
    )
    sync.add_argument(
        "--target-url",
        default=None,
        help="Target Oracle 9Router URL (default: http://100.113.193.124:20128).",
    )
    sync.add_argument(
        "--target-token",
        default=None,
        help="Target Oracle 9Router x-9r-cli-token.",
    )
    sync.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max number of accounts to push.",
    )
    sync.add_argument(
        "--no-probe",
        dest="probe",
        action="store_false",
        default=True,
        help="Skip pre-sync live token probe.",
    )
    sync.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Perform health checks without pushing to Oracle 9Router.",
    )

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(verbose=args.verbose)
    signal.signal(signal.SIGINT, _graceful_exit)

    if args.command == "register":
        cmd_register(args)
    elif args.command == "mint":
        cmd_mint(args)
    elif args.command == "audit":
        cmd_audit(args)
    elif args.command in ("sync-oracle", "push-oracle"):
        cmd_sync_oracle(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
