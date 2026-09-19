#!/usr/bin/env python3
import json
import os
import time
import requests

# List all 9Router ports
PORTS = [20128, 20129, 20130, 20131]

def push_to_port(port, email, sso_token):
    url = f"http://localhost:{port}/api/providers"
    payload = {
        "provider": "grok-web",
        "name": email,
        "apiKey": sso_token,
        "status": "active"
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code in [200, 201]:
            print(f"[+] {email} → port {port} SUCCESS")
            return True
        else:
            print(f"[-] {email} → port {port} {resp.status_code}: {resp.text}")
            return False
    except Exception as e:
        print(f"[-] {email} → port {port} ERROR: {e}")
        return False

def main():
    accounts_file = "accounts.txt"
    if not os.path.exists(accounts_file):
        print("[-] File accounts.txt tidak ditemukan")
        return
    
    with open(accounts_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or "----" not in line:
                continue
                
            try:
                email, pw, sso = line.split("----")
                if not sso or sso == "N/A":
                    print(f"[-] {email} skip (no SSO)")
                    continue
                    
                print(f"\n[+] Pushing {email}")
                
                success = False
                for port in PORTS:
                    if push_to_port(port, email, sso):
                        success = True
                        break
                    time.sleep(1)
                    
                if not success:
                    print(f"[-] {email} gagal push ke semua port")
                    
            except Exception as e:
                print(f"[-] Error parsing {email}: {e}")

if __name__ == "__main__":
    main()