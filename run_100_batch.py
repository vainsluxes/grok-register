#!/usr/bin/env python3
import json
import os
import sys
import subprocess
import time
import urllib.request
from datetime import datetime, timedelta

LOG_PATH = "/home/ubuntu/grok-register/register_100.log"
DISCORD_CHANNEL_ID = "1541841037784383588"

# Check how many accounts were already completed in the log
already_success = 0
if os.path.exists(LOG_PATH):
    with open(LOG_PATH, "r", encoding="utf-8") as f:
        for line in f:
            if "[+] Registration and save succeeded" in line:
                already_success += 1

# If count passed via CLI arg, use it; otherwise calculate remaining to reach 100
if len(sys.argv) > 1:
    target_count = int(sys.argv[1])
else:
    target_count = max(1, 100 - already_success)

# Read bot token from local .env if available
DISCORD_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
if not DISCORD_TOKEN and os.path.exists("/home/ubuntu/grok-register/.env"):
    try:
        with open("/home/ubuntu/grok-register/.env") as f:
            for line in f:
                if line.startswith("DISCORD_BOT_TOKEN="):
                    DISCORD_TOKEN = line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass

start_time = datetime.now()
print(f"[*] Resuming registration batch at {start_time.strftime('%Y-%m-%d %H:%M:%S')}...")
print(f"[*] Already completed: {already_success} accounts")
print(f"[*] Running next: {target_count} accounts to complete batch...")

cmd = [
    "/home/ubuntu/grok-register/.venv/bin/python",
    "/home/ubuntu/grok-register/grok_pipeline.py",
    "-v",
    "register",
    "--count",
    str(target_count),
    "--mint",
    "--sleep",
    "8",
]

# Append mode so previous records are preserved
with open(LOG_PATH, "a", encoding="utf-8") as log_file:
    log_file.write(f"\n--- Resumed at {start_time.strftime('%Y-%m-%d %H:%M:%S')} for {target_count} accounts ---\n")
    log_file.flush()
    proc = subprocess.Popen(
        cmd,
        cwd="/home/ubuntu/grok-register",
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    proc.wait()

end_time = datetime.now()
duration = end_time - start_time
duration_str = str(timedelta(seconds=int(duration.total_seconds())))

# Analyze total results from log
total_success = 0
total_fail = 0
if os.path.exists(LOG_PATH):
    with open(LOG_PATH, "r", encoding="utf-8") as f:
        for line in f:
            if "[+] Registration and save succeeded" in line:
                total_success += 1
            elif "[-] Registration failed" in line:
                total_fail += 1

# Total accounts count in accounts.txt
total_acc = 0
acc_file = "/home/ubuntu/grok-register/accounts.txt"
if os.path.exists(acc_file):
    with open(acc_file, "r", encoding="utf-8") as f:
        total_acc = sum(1 for line in f if "----" in line)

# Query local 9router DB count
grok_cli_count = 0
try:
    import sqlite3
    conn = sqlite3.connect("/home/ubuntu/.9router/db/data.sqlite")
    c = conn.cursor()
    c.execute("SELECT count(*) FROM providerConnections WHERE provider='grok-cli'")
    grok_cli_count = c.fetchone()[0]
    conn.close()
except Exception:
    pass

report = (
    f"**Laporan Registrasi Grok xAI (Batch 100 Akun - Tencent SG)**\n\n"
    f"• Target Batch: 100 akun\n"
    f"• Total Berhasil: {total_success} akun\n"
    f"• Total Gagal: {total_fail} akun\n"
    f"• Durasi Sesi Lanjutan: {duration_str}\n"
    f"• Total Akun di accounts.txt: {total_acc}\n"
    f"• Total di 9Router Staging: {grok_cli_count} akun grok-cli\n"
    f"• Node: Tencent SG (101.32.245.114 via Cloudflare WARP SG)\n\n"
    f"Semua akun yang berhasil sudah tersimpan di staging Tencent SG dan siap di-sync ke 9Router Oracle."
)

print("\n" + "=" * 60)
print(report)
print("=" * 60)

# Send to Discord
if DISCORD_TOKEN:
    try:
        url = f"https://discord.com/api/v10/channels/{DISCORD_CHANNEL_ID}/messages"
        req = urllib.request.Request(
            url,
            data=json.dumps({"content": report}).encode("utf-8"),
            headers={
                "Authorization": f"Bot {DISCORD_TOKEN}",
                "Content-Type": "application/json",
                "User-Agent": "WaguriAgent/1.0"
            }
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status in (200, 201):
                print(f"[+] Successfully delivered report to Discord channel #{DISCORD_CHANNEL_ID}")
    except Exception as e:
        print(f"[!] Failed to deliver Discord report: {e}")
else:
    print("[!] Warning: DISCORD_BOT_TOKEN not provided, skipped sending report.")
