import sqlite3
import json
import uuid
import sys
import os
import glob
from datetime import datetime, timezone

DB_PATH = os.path.expanduser("~/.9router/db/data.sqlite")

def insert_grok_cli(email, auth):
    if not os.path.exists(DB_PATH):
        print(f"[!] 9Router DB not found: {DB_PATH}")
        return False
        
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Check duplicate
    cursor.execute("SELECT id FROM providerConnections WHERE provider='grok-cli' AND email=?", (email,))
    if cursor.fetchone():
        conn.close()
        print(f"[*] 9Router grok-cli: {email} already exists, skipping")
        return True
        
    now = datetime.now(timezone.utc).isoformat()
    conn_id = str(uuid.uuid4())
    
    data = {
        "displayName": email.split("@")[0],
        "accessToken": auth.get("access_token", ""),
        "refreshToken": auth.get("refresh_token", ""),
        "expiresAt": auth.get("expired", ""),
        "scope": "openid profile email offline_access grok-cli:access api:access conversations:read conversations:write",
        "testStatus": "unknown",
        "expiresIn": auth.get("expires_in", 21600),
        "providerSpecificData": {
            "authMethod": "device_code",
            "idToken": auth.get("id_token", ""),
            "email": email,
            "userId": auth.get("sub", ""),
            "hasGrokCodeAccess": True,
            "subscriptionTier": None,
        },
        "lastError": None,
        "lastErrorAt": None,
    }
    
    cursor.execute(
        """INSERT INTO providerConnections 
        (id, provider, authType, name, email, priority, isActive, data, createdAt, updatedAt)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (conn_id, "grok-cli", "oauth", email, email, 1, 1, json.dumps(data), now, now),
    )
    conn.commit()
    conn.close()
    print(f"[+] 9Router grok-cli inserted: {email}")
    return True

def insert_grok_web(email, sso_token):
    if not os.path.exists(DB_PATH):
        return False
        
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT id FROM providerConnections WHERE provider='grok-web' AND name=?", (email,))
    if cursor.fetchone():
        conn.close()
        print(f"[*] 9Router grok-web: {email} already exists, skipping")
        return True
        
    now = datetime.now(timezone.utc).isoformat()
    conn_id = str(uuid.uuid4())
    
    data = {
        "apiKey": sso_token,
        "testStatus": "unknown",
        "providerSpecificData": {
            "connectionProxyEnabled": False,
            "connectionProxyUrl": "",
            "connectionNoProxy": "",
        }
    }
    
    cursor.execute(
        """INSERT INTO providerConnections
        (id, provider, authType, name, email, priority, isActive, data, createdAt, updatedAt)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (conn_id, "grok-web", "cookie", email, None, 1, 1, json.dumps(data), now, now),
    )
    conn.commit()
    conn.close()
    print(f"[+] 9Router grok-web inserted: {email}")
    return True

def main():
    # Read files in cpa_auths
    cpa_dir = os.path.expanduser("~/grok-register/cpa_auths")
    payload_path = os.path.expanduser("~/grok-register/sync_payload.json")
    
    if os.path.exists(payload_path):
        print(f"[*] Importing from sync_payload.json...")
        try:
            with open(payload_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            
            oidc_data = payload.get("oidc", {})
            sso_data = payload.get("sso", {})
            
            for email, auth in oidc_data.items():
                insert_grok_cli(email, auth)
                
            for email, sso in sso_data.items():
                insert_grok_web(email, sso)
                
            print("[+] Sync payload import done.")
            return
        except Exception as e:
            print(f"[!] Failed to import sync_payload.json: {e}")
            # fall back to local scan

    if not os.path.exists(cpa_dir):
        print(f"[!] cpa_auths directory not found: {cpa_dir}")
        return

    # Check for accounts_*.txt file to get SSO tokens
    accounts_files = glob.glob(os.path.expanduser("~/grok-register/accounts_*.txt"))
    sso_map = {}
    for fpath in accounts_files:
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                for line in f:
                    parts = line.strip().split("----")
                    if len(parts) >= 3 and parts[2] and parts[2] != "N/A":
                        sso_map[parts[0]] = parts[2]
        except Exception:
            pass

    files = glob.glob(os.path.join(cpa_dir, "xai-*.json"))
    print(f"[*] Found {len(files)} CPA OIDC credentials files")
    
    for path in files:
        try:
            with open(path, "r", encoding="utf-8") as f:
                auth = json.load(f)
            email = auth.get("email")
            if not email:
                continue
            
            # 1. Insert grok-cli
            insert_grok_cli(email, auth)
            
            # 2. Insert grok-web if we have sso cookie
            sso = sso_map.get(email)
            if sso:
                insert_grok_web(email, sso)
                
        except Exception as e:
            print(f"[!] Error processing {path}: {e}")

if __name__ == "__main__":
    main()
