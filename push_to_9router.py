"""Auto-push Grok SSO tokens to 9Router after registration."""
import json
import os
import glob
import time
import sys

try:
    from curl_cffi import requests
except ImportError:
    import requests

ROUTER_BASE = "http://localhost:20128"
PROVIDERS = ["grok-web"]  # only grok-web accepts SSO tokens


def get_existing_keys():
    """Get existing xai connections from 9Router."""
    try:
        resp = requests.get(f"{ROUTER_BASE}/api/providers", timeout=10)
        data = resp.json()
        return {
            c.get("name", "")
            for c in data.get("connections", [])
            if c.get("provider") == "xai"
        }
    except Exception:
        return set()


def push_token(email, sso_token):
    """Push a single SSO token to 9Router (all providers)."""
    results = []
    for provider in PROVIDERS:
        resp = requests.post(
            f"{ROUTER_BASE}/api/providers",
            json={
                "provider": provider,
                "name": email,
                "apiKey": sso_token,
            },
            timeout=10,
        )
        results.append((provider, resp.json()))
    return results


def parse_accounts_file(path):
    """Parse accounts file (email----password----sso_token)."""
    accounts = []
    if not os.path.exists(path):
        return accounts
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split("----")
            if len(parts) >= 3:
                email, password, token = parts[0], parts[1], parts[2]
                if token and token != "N/A":
                    accounts.append((email, token))
    return accounts


def find_latest_accounts_file():
    """Find the most recent accounts_*.txt file."""
    files = glob.glob("accounts_*.txt")
    if not files:
        return None
    return max(files, key=os.path.getmtime)


def main():
    watch = "--watch" in sys.argv
    
    if watch:
        print(f"[*] Watching for new accounts to push to9Router ({ROUTER_BASE})")
        last_count = 0
        while True:
            path = find_latest_accounts_file()
            if path:
                accounts = parse_accounts_file(path)
                existing = get_existing_keys()
                new_accounts = [(e, t) for e, t in accounts if e not in existing]
                for email, token in new_accounts:
                    print(f"[*] Pushing: {email}")
                    results = push_token(email, token)
                    for provider, result in results:
                        if "error" in result:
                            print(f"[!] {provider}: {result['error']}")
                        else:
                            print(f"[+] {provider}: {email}")
                last_count = len(accounts)
            time.sleep(5)
    else:
        # One-shot mode
        path = find_latest_accounts_file()
        if not path:
            print("[!] No accounts_*.txt file found")
            sys.exit(1)
        
        print(f"[*] Reading: {path}")
        accounts = parse_accounts_file(path)
        existing = get_existing_keys()
        new_accounts = [(e, t) for e, t in accounts if e not in existing]
        
        print(f"[*] Found {len(accounts)} accounts, {len(new_accounts)} new")
        
        for email, token in new_accounts:
            print(f"[*] Pushing: {email}")
            results = push_token(email, token)
            for provider, result in results:
                if "error" in result:
                    print(f"[!] {provider}: {result['error']}")
                else:
                    print(f"[+] {provider}: {email}")
        
        print(f"[*] Done. {len(new_accounts)} tokens pushed to9Router.")


if __name__ == "__main__":
    main()
