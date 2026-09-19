#!/usr/bin/env python3
"""Syncs locally generated cpa_auths and SSO accounts to the remote Oracle VPS.

It generates a single self-contained JSON dump, sends it to the VPS Oracle using scp
(or allows the user to copy-paste), and imports it on the VPS side directly into SQLite.
"""
import os
import sys
import json
import glob
import subprocess

REG_DIR = os.path.dirname(os.path.abspath(__file__))

def main():
    print("[*] Gathering credentials to sync...")
    
    # 1. Gather OIDC credentials
    cpa_dir = os.path.join(REG_DIR, "cpa_auths")
    files = glob.glob(os.path.join(cpa_dir, "xai-*.json"))
    
    oidc_data = {}
    for path in files:
        try:
            with open(path, "r", encoding="utf-8") as f:
                auth = json.load(f)
            email = auth.get("email")
            if email:
                oidc_data[email] = auth
        except Exception as e:
            print(f"[!] Error reading {path}: {e}")
            
    # 2. Gather SSO tokens
    accounts_files = glob.glob(os.path.join(REG_DIR, "accounts_*.txt"))
    sso_data = {}
    for fpath in accounts_files:
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                for line in f:
                    parts = line.strip().split("----")
                    if len(parts) >= 3 and parts[2] and parts[2] != "N/A":
                        sso_data[parts[0]] = parts[2]
        except Exception:
            pass

    payload = {
        "oidc": oidc_data,
        "sso": sso_data
    }
    
    dump_path = os.path.join(REG_DIR, "sync_payload.json")
    with open(dump_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        
    print(f"[+] Local payload generated at: {dump_path}")
    print(f"    Total OIDC accounts: {len(oidc_data)}")
    print(f"    Total SSO cookies: {len(sso_data)}")
    
    # Instructions
    print("\nUntuk mengirim dan meng-import data ke VPS Oracle (100.95.144.85), jalankan:")
    print("--------------------------------------------------------------------------")
    print(f"1. Kirim file ke VPS:")
    print(f"   scp {dump_path} 100.95.144.85:~/grok-register/")
    print(f"2. SSH ke VPS:")
    print(f"   ssh 100.95.144.85")
    print(f"3. Jalankan import script di VPS:")
    print(f"   python3 ~/grok-register/import_to_vps_db.py")
    print("--------------------------------------------------------------------------")

if __name__ == "__main__":
    main()
