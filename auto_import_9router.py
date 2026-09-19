"""Run grok-register + auto-push to 9Router simultaneously."""
import subprocess
import sys
import os
import time
import glob
import json

try:
    from curl_cffi import requests
except ImportError:
    import requests

ROUTER_BASE = "http://localhost:20128"
PROVIDERS = ["grok-web"]  # only grok-web accepts SSO tokens
REG_DIR = os.path.dirname(os.path.abspath(__file__))


def get_existing_keys():
    try:
        resp = requests.get(f"{ROUTER_BASE}/api/providers", timeout=10)
        return {c.get("name", "") for c in resp.json().get("connections", []) if c.get("provider") in PROVIDERS}
    except Exception:
        return set()


def push_token(email, sso_token):
    results = []
    for provider in PROVIDERS:
        try:
            resp = requests.post(
                f"{ROUTER_BASE}/api/providers",
                json={"provider": provider, "name": email, "apiKey": sso_token},
                timeout=10,
            )
            results.append((provider, resp.json()))
        except Exception as e:
            results.append((provider, {"error": str(e)}))
    return results


def parse_accounts_file(path):
    accounts = []
    if not os.path.exists(path):
        return accounts
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            parts = line.strip().split("----")
            if len(parts) >= 3 and parts[2] and parts[2] != "N/A":
                accounts.append((parts[0], parts[2]))
    return accounts


def find_latest_accounts_file():
    files = glob.glob(os.path.join(REG_DIR, "accounts_*.txt"))
    if not files:
        return None
    return max(files, key=os.path.getmtime)


def watch_and_push():
    """Watch for new accounts and push to 9Router."""
    print("[*] Auto-push to 9Router started")
    last_file = None
    last_count = 0
    while True:
        try:
            path = find_latest_accounts_file()
            if path and path != last_file:
                last_file = path
                last_count = 0
            if path:
                accounts = parse_accounts_file(path)
                if len(accounts) > last_count:
                    existing = get_existing_keys()
                    new = [(e, t) for e, t in accounts[last_count:] if e not in existing]
                    for email, token in new:
                        results = push_token(email, token)
                        for provider, result in results:
                            if "error" in result:
                                print(f"[!] 9Router {provider}: {result['error']}")
                            else:
                                print(f"[+] 9Router {provider}: {email}")
                    last_count = len(accounts)
        except Exception as e:
            print(f"[!] 9Router push error: {e}")
        time.sleep(3)


if __name__ == "__main__":
    if "--watch" in sys.argv:
        watch_and_push()
    else:
        # One-shot push
        path = find_latest_accounts_file()
        if not path:
            print("[!] No accounts file found")
            sys.exit(1)
        accounts = parse_accounts_file(path)
        existing = get_existing_keys()
        new = [(e, t) for e, t in accounts if e not in existing]
        print(f"[*] {len(accounts)} accounts, {len(new)} new")
        for email, token in new:
            results = push_token(email, token)
            for provider, result in results:
                if "error" in result:
                    print(f"[!] {provider}: {result['error']}")
                else:
                    print(f"[+] {provider}: {email}")
        print(f"[*] Done. {len(new)} pushed.")
