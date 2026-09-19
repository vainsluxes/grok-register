# Grok Register & Mint Pipeline

## Overview
Automated registration, OAuth device-authorization minting, and 9Router gateway provisioning pipeline for xAI Grok accounts.
Stack: Python 3.11/3.12, DrissionPage, Cloudflare TempMail Worker API, SQLite (9Router DB), Xvfb virtual display.

## Commands
- Setup / Venv: `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`
- Mint existing accounts: `python auto_import_grok_cli.py --limit 10 --sleep 6`
- CLI Mint single: `python cpa_cli.py --email <EMAIL> --password <PASSWORD> -v`
- Run test suite: `pytest tests/`

## Conventions
- Always write robust error handling with explicit timeouts on browser and network requests.
- All code, comments, docstrings, variable names, and logs MUST be in English.
- Use headless/xvfb display mode for background unattended operations (`prepare_browser_display()`).
- Handle Cloudflare Turnstile actively (detect token length >= 80, click shadow-root / iframe input).

## Boundaries
- **NEVER** hardcode plaintext secrets, tokens, or passwords into git/code. Use `<PLACEHOLDER>` or read from environment/config.
- **ALWAYS** sanitize outputs before persisting to disk or logging to terminal.
- **NEVER** clobber `accounts.txt` or `mail_credentials.txt` without append/merge safety.

## Dependencies
- `DrissionPage>=4.0`: Anti-detect browser automation for Chromium.
- `requests` / `urllib3`: HTTP requests to Cloudflare TempMail worker and OAuth endpoints.
- `pytest`: Regression and modularity test suite.

## Config
- `config.json`: TempMail worker endpoint (`cloudflare_api_base`), API key, proxy, and browser hide mode.
- `DB_PATH`: SQLite database path for 9Router (`~/.9router/db/data.sqlite`).

## Error Handling
- Turnstile challenge timeout: retry up to limit with debug screenshot.
- Device authorization rate limits (`HTTP 429 slow_down`): enforce backoff sleep between accounts.
- Mail verification timeout: discard stale address and retry with new address up to `max_mail_retry`.
