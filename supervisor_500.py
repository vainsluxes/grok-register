#!/usr/bin/env python3
import json
import os
import subprocess
import time
import urllib.request
from datetime import datetime, timedelta

LOG_PATH = "/home/ubuntu/grok-register/register_100.log"
DISCORD_CHANNEL_ID = "1541841037784383588"
SUPERVISOR_LOG = "/home/ubuntu/grok-register/supervisor_500.log"
TARGET_TOTAL = 500
CHUNK_SIZE = 50

# Read bot token
DISCORD_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
if not DISCORD_TOKEN and os.path.exists("/home/ubuntu/grok-register/.env"):
    try:
        with open("/home/ubuntu/grok-register/.env") as f:
            for line in f:
                if line.startswith("DISCORD_BOT_TOKEN="):
                    DISCORD_TOKEN = line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass

def slog(msg):
    ts = time.strftime("[%Y-%m-%d %H:%M:%S]")
    line = f"{ts} {msg}"
    print(line, flush=True)
    with open(SUPERVISOR_LOG, "a") as f:
        f.write(line + "\n")

def get_stats():
    success = 0
    fail = 0
    if os.path.exists(LOG_PATH):
        with open(LOG_PATH, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if "[+] Registration and save succeeded" in line:
                    success += 1
                elif "[-] Registration failed" in line:
                    fail += 1

    total_acc = 0
    acc_file = "/home/ubuntu/grok-register/accounts.txt"
    if os.path.exists(acc_file):
        with open(acc_file, "r", encoding="utf-8", errors="ignore") as f:
            total_acc = sum(1 for line in f if "----" in line)

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

    return success, fail, total_acc, grok_cli_count

def send_discord_report(milestone_text, success, fail, total_acc, staging_count, duration_str, is_final=False):
    status_header = "🎉 **FINAL REPORT — Registrasi Grok xAI Selesai!**" if is_final else f"📊 **Milestone Update — {milestone_text}**"
    report = (
        f"{status_header}\n\n"
        f"• Progress Target: {success}/{TARGET_TOTAL} akun\n"
        f"• Total Berhasil: {success} akun\n"
        f"• Total Gagal: {fail} akun\n"
        f"• Durasi Berjalan: {duration_str}\n"
        f"• Total Akun di accounts.txt: {total_acc}\n"
        f"• Total di 9Router Staging: {staging_count} akun grok-cli\n"
        f"• Node: Tencent SG (101.32.245.114 via Cloudflare WARP SG)\n\n"
        f"{'Semua 500 akun selesai tersimpan di staging Tencent SG dan siap di-sync ke 9Router Oracle!' if is_final else 'Proses registrasi otomatis terus berlanjut di background...'}"
    )

    if not DISCORD_TOKEN:
        slog("[!] Warning: No DISCORD_BOT_TOKEN found, skipping Discord message.")
        return

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
                slog(f"[+] Discord report sent successfully: {milestone_text}")
    except Exception as e:
        slog(f"[!] Failed to send Discord report: {e}")

def cleanup_disk():
    slog("[*] Performing disk cleanup in /tmp...")
    os.system("rm -rf /tmp/DrissionPage /tmp/chrome_warp_* /tmp/com.google.Chrome.* 2>/dev/null")
    os.system("journalctl --vacuum-size=20M >/dev/null 2>&1")

def is_tmux_running(session_name):
    res = subprocess.run(["tmux", "has-session", "-t", session_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return res.returncode == 0

def main():
    slog(f"[*] Supervisor 500 started. Target: {TARGET_TOTAL} accounts.")
    start_time = datetime.now()

    # Step 1: Wait for initial batch (grok-batch-100) if active
    if is_tmux_running("grok-batch-100"):
        slog("[*] Detected active session 'grok-batch-100'. Waiting for it to complete the 100 milestone...")
        while is_tmux_running("grok-batch-100"):
            time.sleep(20)
        slog("[+] 'grok-batch-100' completed!")

    last_reported_milestone = 100

    # Step 2: Main loop until TARGET_TOTAL is reached
    while True:
        success, fail, total_acc, staging_count = get_stats()
        slog(f"[*] Current status: {success}/{TARGET_TOTAL} succeeded, {fail} failed.")

        if success >= TARGET_TOTAL:
            slog(f"[+] Target reached! Total succeeded: {success}")
            duration_str = str(timedelta(seconds=int((datetime.now() - start_time).total_seconds())))
            send_discord_report(f"{success}/{TARGET_TOTAL}", success, fail, total_acc, staging_count, duration_str, is_final=True)
            break

        cleanup_disk()

        needed = TARGET_TOTAL - success
        batch_size = min(CHUNK_SIZE, needed)
        slog(f"[*] Launching next chunk of {batch_size} accounts (target: {success + batch_size}/{TARGET_TOTAL})...")

        cmd = [
            "/home/ubuntu/grok-register/.venv/bin/python",
            "/home/ubuntu/grok-register/grok_pipeline.py",
            "-v",
            "register",
            "--count",
            str(batch_size),
            "--mint",
            "--sleep",
            "8",
        ]

        with open(LOG_PATH, "a", encoding="utf-8") as log_file:
            log_file.write(f"\n--- Supervisor Chunk {batch_size} at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---\n")
            log_file.flush()
            proc = subprocess.Popen(
                cmd,
                cwd="/home/ubuntu/grok-register",
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
            proc.wait()

        # Check stats after chunk
        new_success, new_fail, new_total_acc, new_staging = get_stats()
        duration_str = str(timedelta(seconds=int((datetime.now() - start_time).total_seconds())))

        # Send milestone report if passed a 100-boundary
        current_hundred = (new_success // 100) * 100
        if current_hundred > last_reported_milestone and current_hundred < TARGET_TOTAL:
            last_reported_milestone = current_hundred
            send_discord_report(f"{new_success}/{TARGET_TOTAL} Akun", new_success, new_fail, new_total_acc, new_staging, duration_str, is_final=False)

        # Brief sleep between chunks
        time.sleep(5)

if __name__ == "__main__":
    main()
