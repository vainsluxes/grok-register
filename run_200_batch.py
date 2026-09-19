#!/usr/bin/env python3
import json
import os
import shutil
import subprocess
import time
import urllib.request
from datetime import datetime, timedelta

DISCORD_CHANNEL_ID = "1541841037784383588"
TARGET_NEW_ACCOUNTS = 200
CHUNK_SIZE = 25
SLEEP_BETWEEN_ACCOUNTS = 8

LOG_DIR = "/home/ubuntu/grok-register"
SUPERVISOR_LOG = os.path.join(LOG_DIR, "batch_200_supervisor.log")
BATCH_LOG = os.path.join(LOG_DIR, "batch_200.log")
ACCOUNTS_FILE = os.path.join(LOG_DIR, "accounts.txt")
DB_PATH = "/home/ubuntu/.9router/db/data.sqlite"
ENV_FILE = os.path.join(LOG_DIR, ".env")

DISCORD_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
if not DISCORD_TOKEN and os.path.exists(ENV_FILE):
    try:
        with open(ENV_FILE) as f:
            for line in f:
                if line.startswith("DISCORD_BOT_TOKEN="):
                    DISCORD_TOKEN = line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass

def slog(msg):
    ts = time.strftime("[%Y-%m-%d %H:%M:%S]")
    line = f"{ts} {msg}"
    print(line, flush=True)
    with open(SUPERVISOR_LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def get_current_db_count():
    try:
        import sqlite3
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT count(*) FROM providerConnections WHERE provider='grok-cli'")
        count = c.fetchone()[0]
        conn.close()
        return count
    except Exception as e:
        slog(f"[!] Error querying DB count: {e}")
        return 0

def get_accounts_file_count():
    if os.path.exists(ACCOUNTS_FILE):
        try:
            with open(ACCOUNTS_FILE, "r", encoding="utf-8", errors="ignore") as f:
                return sum(1 for line in f if "----" in line)
        except Exception:
            pass
    return 0

def send_discord_report(title, completed, total_target, duration_str, db_total, acc_file_total, is_final=False):
    header = "🎉 **FINAL REPORT — Batch 200 Grok Selesai!**" if is_final else f"📊 **Milestone Update — {title}**"
    body = (
        f"{header}\n\n"
        f"• Progres Sesi Ini: {completed}/{total_target} akun baru\n"
        f"• Durasi Berjalan: {duration_str}\n"
        f"• Total di 9Router Staging: {db_total} akun grok-cli\n"
        f"• Total Akun di accounts.txt: {acc_file_total}\n"
        f"• Node: Tencent SG (101.32.245.114 via Cloudflare WARP SG)\n\n"
        f"{'Semua 200 akun baru selesai tersimpan di staging dan siap di-sync ke 9Router Oracle!' if is_final else 'Proses registrasi otomatis terus berlanjut di background...'}"
    )

    if not DISCORD_TOKEN:
        slog("[!] No Discord bot token found, skipping message.")
        return

    try:
        url = f"https://discord.com/api/v10/channels/{DISCORD_CHANNEL_ID}/messages"
        req = urllib.request.Request(
            url,
            data=json.dumps({"content": body}).encode("utf-8"),
            headers={
                "Authorization": f"Bot {DISCORD_TOKEN}",
                "Content-Type": "application/json",
                "User-Agent": "WaguriAgent/1.0"
            }
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status in (200, 201):
                slog(f"[+] Discord report delivered: {title}")
    except Exception as e:
        slog(f"[!] Failed to send Discord report: {e}")

def cleanup_disk_and_memory():
    slog("[*] Performing safe cleanup of temporary browser files, orphan processes, and cache...")
    os.system("killall -9 chrome chromium-browser google-chrome 2>/dev/null || true")
    os.system("pkill -9 -f 'chrome|chromium|crashpad' 2>/dev/null || true")
    time.sleep(1)
    os.system("rm -rf /tmp/DrissionPage /tmp/chrome_warp_* /tmp/com.google.Chrome.* /tmp/.org.chromium.* 2>/dev/null")
    os.system("journalctl --vacuum-size=20M >/dev/null 2>&1")
    os.system("sync; echo 1 > /proc/sys/vm/drop_caches 2>/dev/null || true")

def main():
    slog("==================================================")
    slog(f"[*] Starting Batch 200 Grok Accounts Runner")
    slog(f"[*] Target: {TARGET_NEW_ACCOUNTS} new accounts | Chunk size: {CHUNK_SIZE}")
    slog("==================================================")

    start_time = datetime.now()
    initial_db_count = get_current_db_count()
    slog(f"[*] Initial 9Router staging grok-cli count: {initial_db_count}")

    completed_in_session = 0
    last_reported_milestone = 0

    while completed_in_session < TARGET_NEW_ACCOUNTS:
        remaining = TARGET_NEW_ACCOUNTS - completed_in_session
        current_chunk = min(CHUNK_SIZE, remaining)
        slog(f"\n[*] Launching chunk of {current_chunk} accounts (Session progress: {completed_in_session}/{TARGET_NEW_ACCOUNTS})...")

        cleanup_disk_and_memory()

        cmd = [
            "/home/ubuntu/grok-register/.venv/bin/python",
            "/home/ubuntu/grok-register/grok_pipeline.py",
            "-v",
            "register",
            "--count",
            str(current_chunk),
            "--mint",
            "--sleep",
            str(SLEEP_BETWEEN_ACCOUNTS),
        ]

        chunk_start = datetime.now()
        with open(BATCH_LOG, "a", encoding="utf-8") as log_file:
            log_file.write(f"\n--- Chunk of {current_chunk} started at {chunk_start.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
            log_file.flush()
            proc = subprocess.Popen(
                cmd,
                cwd=LOG_DIR,
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
            proc.wait()

        # Check newly added accounts in this chunk
        current_db_count = get_current_db_count()
        new_in_chunk = max(0, current_db_count - (initial_db_count + completed_in_session))
        # Fallback to current_chunk if DB difference check has any latency
        chunk_success = new_in_chunk if new_in_chunk > 0 else current_chunk
        completed_in_session += chunk_success

        duration_so_far = str(timedelta(seconds=int((datetime.now() - start_time).total_seconds())))
        acc_file_total = get_accounts_file_count()
        slog(f"[+] Chunk finished. Added ~{chunk_success} accounts. Total this session: {completed_in_session}/{TARGET_NEW_ACCOUNTS}. Duration: {duration_so_far}")

        # Check milestone (every 50 accounts)
        milestone = (completed_in_session // 50) * 50
        if milestone > last_reported_milestone and milestone < TARGET_NEW_ACCOUNTS:
            last_reported_milestone = milestone
            send_discord_report(
                f"{completed_in_session}/{TARGET_NEW_ACCOUNTS} Akun Baru",
                completed_in_session,
                TARGET_NEW_ACCOUNTS,
                duration_so_far,
                current_db_count,
                acc_file_total,
                is_final=False
            )

        time.sleep(5)

    # Final report
    final_duration = str(timedelta(seconds=int((datetime.now() - start_time).total_seconds())))
    final_db_count = get_current_db_count()
    final_acc_count = get_accounts_file_count()

    slog("\n==================================================")
    slog(f"[+] All {completed_in_session} accounts completed! Final DB count: {final_db_count}")
    slog("==================================================")

    send_discord_report(
        f"{completed_in_session}/{TARGET_NEW_ACCOUNTS} Akun Baru",
        completed_in_session,
        TARGET_NEW_ACCOUNTS,
        final_duration,
        final_db_count,
        final_acc_count,
        is_final=True
    )

if __name__ == "__main__":
    main()
