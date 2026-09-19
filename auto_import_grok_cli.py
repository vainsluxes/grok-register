"""Auto-mint OAuth credentials and push to 9Router grok-cli provider."""
import json
import os
import sys
import glob
import sqlite3
import uuid
import time
from datetime import datetime, timezone

# Add project root to path
REG_DIR = os.path.dirname(os.path.abspath(__file__))
if REG_DIR not in sys.path:
    sys.path.insert(0, REG_DIR)

DB_PATH = os.environ.get("NINE_ROUTER_DB") or os.path.expanduser("~/.9router/db/data.sqlite")


def get_existing_emails():
    """Get emails already in 9Router grok-cli or already minted to cpa_auths."""
    emails = set()
    if os.path.exists(DB_PATH):
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
                ps = (d.get("providerSpecificData") or {})
                e = (ps.get("email") or d.get("email") or "").strip().lower()
                if e and "@" in e:
                    emails.add(e)
            except Exception:
                pass
        conn.close()

    auth_dir = os.path.join(REG_DIR, "cpa_auths")
    for path in glob.glob(os.path.join(auth_dir, "xai-*.json")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
            e = str(d.get("email") or "").strip().lower()
            if e and "@" in e:
                emails.add(e)
        except Exception:
            # Fallback: xai-email@domain.json
            base = os.path.basename(path)
            if base.startswith("xai-") and base.endswith(".json"):
                emails.add(base[4:-5].lower())
    return emails


def parse_accounts(path):
    """Parse accounts file (email----password----sso_token).

    Supports both:
      email----password----sso
      N|email----password----sso
    """
    accounts = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
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
                if "@" in email:
                    accounts.append({"email": email, "password": password})
    return accounts


def find_latest_accounts():
    """Find accounts.txt or fallback to most recent accounts_*.txt."""
    single = os.path.join(REG_DIR, "accounts.txt")
    if os.path.exists(single):
        return single
    files = glob.glob(os.path.join(REG_DIR, "accounts_*.txt"))
    if not files:
        return None
    return max(files, key=os.path.getmtime)


def mint_oauth(email, password, save_auth_file=False):
    """Mint OAuth credentials via device flow.

    By default does NOT write cpa_auths JSON — returns tokens in-memory and
    the caller inserts them into 9Router directly.
    """
    from cpa_xai.mint import mint_and_export

    auth_dir = os.path.join(REG_DIR, "cpa_auths")
    if save_auth_file:
        os.makedirs(auth_dir, exist_ok=True)

    proxy = None
    try:
        from app_config import load_config
        cfg = load_config()
        proxy = cfg.get("cpa_proxy") or cfg.get("proxy") or None
    except Exception:
        pass

    result = mint_and_export(
        email=email,
        password=password,
        auth_dir=auth_dir,
        proxy=proxy,
        headless=False,
        browser_timeout_sec=120,
        force_standalone=True,
        reuse_browser=True,
        recycle_every=15,
        log=lambda msg: print(f"  [{email}] {msg}", flush=True),
        request_timeout_sec=15,
        poll_timeout_sec=15,
        save_auth_file=True,
    )
    return result


def insert_to_9router(email, token_data):
    """Insert or update grok-cli connection into 9Router SQLite."""
    if not os.path.exists(DB_PATH):
        print(f"[!] 9Router DB not found: {DB_PATH}")
        return False

    email = str(email or "").strip()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    now = datetime.now(timezone.utc).isoformat()

    # Prefer flat access_token fields; also accept nested CPA schema variants.
    access_token = (
        token_data.get("access_token")
        or token_data.get("accessToken")
        or (token_data.get("tokens") or {}).get("access_token")
        or ""
    )
    refresh_token = (
        token_data.get("refresh_token")
        or token_data.get("refreshToken")
        or (token_data.get("tokens") or {}).get("refresh_token")
        or ""
    )
    id_token = (
        token_data.get("id_token")
        or token_data.get("idToken")
        or (token_data.get("tokens") or {}).get("id_token")
        or ""
    )
    expires_at = (
        token_data.get("expires_at")
        or token_data.get("expiresAt")
        or token_data.get("expired")
        or ""
    )
    expires_in = token_data.get("expires_in") or token_data.get("expiresIn") or 21600

    data = {
        "displayName": token_data.get("displayName", "") or email,
        "accessToken": access_token,
        "refreshToken": refresh_token,
        "expiresAt": expires_at,
        "scope": "openid profile email offline_access grok-cli:access api:access conversations:read conversations:write",
        "testStatus": "unknown",
        "expiresIn": expires_in,
        "providerSpecificData": {
            "authMethod": "device_code",
            "idToken": id_token,
            "email": email,
            "userId": token_data.get("user_id", "") or token_data.get("userId", ""),
            "hasGrokCodeAccess": True,
            "subscriptionTier": None,
        },
        "lastError": None,
        "lastErrorAt": None,
    }

    existing = cursor.execute(
        "SELECT id FROM providerConnections WHERE provider='grok-cli' AND (email=? OR name=?)",
        (email, email),
    ).fetchone()

    if existing:
        cursor.execute(
            """UPDATE providerConnections
               SET authType=?, name=?, email=?, priority=?, isActive=?, data=?, updatedAt=?
               WHERE id=?""",
            (
                "oauth",
                email,
                email,
                1,
                1,
                json.dumps(data),
                now,
                existing[0],
            ),
        )
    else:
        conn_id = str(uuid.uuid4())
        cursor.execute(
            """INSERT INTO providerConnections
            (id, provider, authType, name, email, priority, isActive, data, createdAt, updatedAt)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                conn_id,
                "grok-cli",
                "oauth",
                email,
                email,
                1,
                1,
                json.dumps(data),
                now,
                now,
            ),
        )
    conn.commit()
    conn.close()
    return True


def read_cpa_auth(email):
    """Read minted CPA auth file for an email."""
    safe_email = "".join(c if (c.isalnum() or c in "@._-") else "-" for c in email)
    auth_dir = os.path.join(REG_DIR, "cpa_auths")
    path = os.path.join(auth_dir, f"xai-{safe_email}.json")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _parse_limit(argv):
    """Parse --limit N from argv. Default None = no limit."""
    for i, arg in enumerate(argv):
        if arg == "--limit" and i + 1 < len(argv):
            try:
                return max(1, int(argv[i + 1]))
            except ValueError:
                return None
        if arg.startswith("--limit="):
            try:
                return max(1, int(arg.split("=", 1)[1]))
            except ValueError:
                return None
    return None


def _parse_sleep(argv):
    """Parse --sleep SEC between mints. Default 8."""
    for i, arg in enumerate(argv):
        if arg == "--sleep" and i + 1 < len(argv):
            try:
                return max(0.0, float(argv[i + 1]))
            except ValueError:
                return 8.0
        if arg.startswith("--sleep="):
            try:
                return max(0.0, float(arg.split("=", 1)[1]))
            except ValueError:
                return 8.0
    return 8.0


def process_account(acc, sleep_sec=0.0, save_auth_file=False):
    """Mint one account and insert into 9Router. Returns True on success."""
    email = acc["email"]
    print(f"\n[*] Minting OAuth for: {email}")
    result = mint_oauth(email, acc["password"], save_auth_file=save_auth_file)
    if result.get("ok"):
        # Prefer in-memory tokens from mint; fall back to local CPA file.
        auth = None
        if result.get("access_token"):
            auth = result
        else:
            auth = read_cpa_auth(email)
        if auth:
            if insert_to_9router(email, auth):
                print(f"[+] {email} -> 9Router grok-cli")
                if sleep_sec:
                    time.sleep(sleep_sec)
                return True
            print(f"[!] {email} DB insert failed")
        else:
            print(f"[!] {email} no tokens and no auth file")
    else:
        print(f"[!] {email} mint failed: {result.get('error')}")
    if sleep_sec:
        time.sleep(sleep_sec)
    return False


def main():
    argv = sys.argv[1:]
    watch = "--watch" in argv
    limit = _parse_limit(argv)
    sleep_sec = _parse_sleep(argv)
    save_auth_file = "--save-cpa" in argv or "--save_auth_file" in argv

    if not os.path.exists(DB_PATH):
        print(f"[!] 9Router DB not found: {DB_PATH}")
        print("[!] Make sure 9Router is installed and has been run at least once")
        sys.exit(1)

    print(f"[*] 9Router DB: {DB_PATH}")
    print(
        f"[*] sleep={sleep_sec}s"
        + (f", limit={limit}" if limit else "")
        + f", save_cpa={save_auth_file}"
    )

    if watch:
        print("[*] Watch mode: monitoring for new accounts")
        last_count = 0
        while True:
            path = find_latest_accounts()
            if path:
                accounts = parse_accounts(path)
                if len(accounts) > last_count:
                    existing = get_existing_emails()
                    new = [
                        a
                        for a in accounts[last_count:]
                        if a["email"].strip().lower() not in existing
                    ]
                    for acc in new:
                        process_account(
                            acc, sleep_sec=sleep_sec, save_auth_file=save_auth_file
                        )
                    last_count = len(accounts)
            time.sleep(5)
    else:
        # One-shot
        path = find_latest_accounts()
        if not path:
            print("[!] No accounts_*.txt found")
            sys.exit(1)

        accounts = parse_accounts(path)
        existing = get_existing_emails()
        new = [a for a in accounts if a["email"].strip().lower() not in existing]
        if limit is not None:
            new = new[:limit]

        print(f"[*] accounts file: {path}")
        print(f"[*] {len(accounts)} accounts, {len(new)} to mint")

        ok = 0
        fail = 0
        for acc in new:
            if process_account(acc, sleep_sec=sleep_sec, save_auth_file=save_auth_file):
                ok += 1
            else:
                fail += 1

        print(f"\n[*] Done. ok={ok} fail={fail}")


if __name__ == "__main__":
    main()
