#!/usr/bin/env python3
"""Continuous 500-Batch Grok Accounts Runner on Tencent SG.

Workflow:
1. Registers & mints accounts in chunks of 25.
2. Reports milestone progress to Discord every 50 accounts.
3. Once 500 verified accounts are in local staging DB:
   a. Bundles all minted OIDC tokens (cpa_auths/xai-*.json).
   b. Pushes directly to Oracle 9Router sync endpoint (http://100.113.193.124:8769/sync).
   c. Upon confirmed sync, archives and resets:
      - accounts.txt (cleared)
      - mail_credentials.txt (cleared)
      - cpa_auths/ (archived and emptied)
      - screenshots/ (emptied)
      - local SQLite grok connections (deleted)
   d. Sends Discord completion report with total Oracle fleet count.
   e. Loops immediately to the next clean 500 batch!
"""

import glob
import json
import os
import shutil
import sqlite3
import subprocess
import time
import urllib.request
from datetime import datetime, timedelta

DISCORD_CHANNEL_ID = "1541841037784383588"
TARGET_NEW_ACCOUNTS = 500
CHUNK_SIZE = 25
SLEEP_BETWEEN_ACCOUNTS = 8

ORACLE_SYNC_URL = "http://100.113.193.124:8769/sync"
ORACLE_COUNT_URL = "http://100.113.193.124:8769/count"
ORACLE_SYNC_TOKEN = "vains-grok-sync-2026-auth"

LOG_DIR = "/home/ubuntu/grok-register"
SUPERVISOR_LOG = os.path.join(LOG_DIR, "batch_500_supervisor.log")
BATCH_LOG = os.path.join(LOG_DIR, "batch_500.log")
ACCOUNTS_FILE = os.path.join(LOG_DIR, "accounts.txt")
MAIL_CREDENTIALS_FILE = os.path.join(LOG_DIR, "mail_credentials.txt")
CPA_DIR = os.path.join(LOG_DIR, "cpa_auths")
SCREENSHOTS_DIR = os.path.join(LOG_DIR, "screenshots")
ARCHIVE_DIR = os.path.join(LOG_DIR, "archive")
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
    try:
        with open(SUPERVISOR_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def get_current_db_count():
    try:
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


def get_oracle_fleet_count():
    try:
        req = urllib.request.Request(ORACLE_COUNT_URL, headers={"User-Agent": "WaguriAgent/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("total_grok_cli", "Unknown")
    except Exception as e:
        slog(f"[!] Failed to get Oracle fleet count: {e}")
        return "Unknown"


def send_discord_report(title, completed, total_target, duration_str, db_total, acc_file_total, oracle_total=None, is_final=False):
    if is_final:
        header = "🎉 **FINAL REPORT — Batch 500 Grok Selesai & Ter-sync!**"
        body = (
            f"{header}\n\n"
            f"• Akun Baru Batch Ini: {completed}/{total_target} akun\n"
            f"• Durasi Pengerjaan: {duration_str}\n"
            f"• Status Sync: Berhasil di-push ke 9Router Oracle VPS\n"
            f"• Total Fleet Grok di Oracle: **{oracle_total} akun**\n"
            f"• Cleanup Staging: `accounts.txt`, `cpa_auths`, dan DB SG di-reset bersih\n\n"
            f"🚀 *Memulai batch 500 berikutnya secara otomatis...*"
        )
    else:
        header = f"📊 **Milestone Update — {title}**"
        body = (
            f"{header}\n\n"
            f"• Progres Sesi Ini: {completed}/{total_target} akun baru\n"
            f"• Durasi Berjalan: {duration_str}\n"
            f"• Total di Staging SG: {db_total} akun grok-cli\n"
            f"• Total di accounts.txt: {acc_file_total}\n"
            f"• Node: Tencent SG (101.32.245.114 via Cloudflare WARP SG)\n\n"
            f"Proses registrasi dan minting otomatis terus berlanjut di background..."
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


def cleanup_runtime():
    slog("[*] Performing safe cleanup of temporary browser files, orphan processes, and cache...")
    os.system("killall -9 chrome chromium-browser google-chrome 2>/dev/null || true")
    os.system("pkill -9 -f 'chrome|chromium|crashpad' 2>/dev/null || true")
    time.sleep(1)
    os.system("rm -rf /tmp/DrissionPage /tmp/chrome_warp_* /tmp/com.google.Chrome.* /tmp/.org.chromium.* 2>/dev/null")
    os.system("journalctl --vacuum-size=20M >/dev/null 2>&1 || true")


def sync_batch_to_oracle():
    slog("[*] Collecting minted tokens for sync to Oracle...")
    accounts_payload = []
    cpa_files = glob.glob(os.path.join(CPA_DIR, "xai-*.json"))
    for p in cpa_files:
        try:
            with open(p, "r", encoding="utf-8") as fh:
                auth = json.load(fh)
            email = auth.get("email")
            if email:
                accounts_payload.append({"email": email, "auth": auth})
        except Exception as e:
            slog(f"[!] Error reading {p}: {e}")

    # Fallback to staging SQLite DB
    if not accounts_payload and os.path.exists(DB_PATH):
        try:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute("SELECT name, email, data FROM providerConnections WHERE provider='grok-cli'")
            for name, email, data in cur.fetchall():
                em = (email or name or "").strip().lower()
                if not em:
                    continue
                d = json.loads(data) if isinstance(data, str) else (data or {})
                ps = d.get("providerSpecificData") or {}
                auth_dict = {
                    "access_token": d.get("accessToken", ""),
                    "refresh_token": d.get("refreshToken", ""),
                    "expired": d.get("expiresAt", ""),
                    "expires_in": d.get("expiresIn", 21600),
                    "id_token": ps.get("idToken", ""),
                    "sub": ps.get("userId", ""),
                    "email": em,
                }
                accounts_payload.append({"email": em, "auth": auth_dict})
            conn.close()
            slog(f"[*] Recovered {len(accounts_payload)} accounts directly from staging SQLite DB.")
        except Exception as e:
            slog(f"[!] Error reading staging DB for sync: {e}")

    if not accounts_payload:
        slog("[!] No accounts found in cpa_auths or staging SQLite DB!")
        return False, 0

    slog(f"[*] Sending {len(accounts_payload)} accounts to Oracle receiver ({ORACLE_SYNC_URL})...")
    payload = {"accounts": accounts_payload}
    try:
        req = urllib.request.Request(
            ORACLE_SYNC_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-sync-token": ORACLE_SYNC_TOKEN,
                "User-Agent": "WaguriAgent-SG/1.0",
            },
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            res_data = json.loads(resp.read().decode("utf-8"))
            if res_data.get("ok"):
                inserted = res_data.get("inserted", 0)
                total_db = res_data.get("total_grok_cli", 0)
                slog(f"[+] Successfully synced {len(accounts_payload)} accounts to Oracle! (New inserted: {inserted}, Total Oracle fleet: {total_db})")
                return True, total_db
            else:
                slog(f"[-] Oracle sync returned error: {res_data}")
                return False, 0
    except Exception as e:
        slog(f"[!] Failed to sync to Oracle: {e}")
        return False, 0


def archive_and_reset_staging():
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    target_archive = os.path.join(ARCHIVE_DIR, f"batch_500_{ts}")
    slog(f"[*] Archiving current batch to {target_archive}...")
    os.makedirs(target_archive, exist_ok=True)

    # Archive cpa_auths
    if os.path.exists(CPA_DIR):
        archive_cpa = os.path.join(target_archive, "cpa_auths")
        shutil.copytree(CPA_DIR, archive_cpa, dirs_exist_ok=True)
        # Clear cpa_auths contents
        for f in glob.glob(os.path.join(CPA_DIR, "*")):
            try:
                if os.path.isfile(f) or os.path.islink(f):
                    os.unlink(f)
                elif os.path.isdir(f):
                    shutil.rmtree(f)
            except Exception:
                pass

    # Archive accounts.txt
    if os.path.exists(ACCOUNTS_FILE):
        try:
            shutil.copy2(ACCOUNTS_FILE, os.path.join(target_archive, "accounts.txt"))
            with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
                pass
        except Exception as e:
            slog(f"[!] Error archiving accounts.txt: {e}")

    # Archive mail_credentials.txt
    if os.path.exists(MAIL_CREDENTIALS_FILE):
        try:
            shutil.copy2(MAIL_CREDENTIALS_FILE, os.path.join(target_archive, "mail_credentials.txt"))
            with open(MAIL_CREDENTIALS_FILE, "w", encoding="utf-8") as f:
                pass
        except Exception as e:
            slog(f"[!] Error archiving mail_credentials.txt: {e}")

    # Clear screenshots
    if os.path.exists(SCREENSHOTS_DIR):
        for f in glob.glob(os.path.join(SCREENSHOTS_DIR, "*")):
            try:
                os.unlink(f)
            except Exception:
                pass

    # Clear staging SQLite DB grok connections
    if os.path.exists(DB_PATH):
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.execute("DELETE FROM providerConnections WHERE provider IN ('grok-cli', 'grok-web')")
            conn.commit()
            conn.close()
            slog("[+] Staging 9Router SQLite grok records purged.")
        except Exception as e:
            slog(f"[!] Error purging staging DB: {e}")

    slog("[+] Staging environment reset complete.")


def run_single_500_batch(batch_number):
    slog("==================================================")
    slog(f"[*] Starting Batch #{batch_number} (Target: {TARGET_NEW_ACCOUNTS} accounts)")
    slog("==================================================")

    start_time = datetime.now()
    initial_db_count = get_current_db_count()
    slog(f"[*] Initial staging grok-cli count: {initial_db_count}")

    completed_in_session = 0
    last_reported_milestone = 0

    while completed_in_session < TARGET_NEW_ACCOUNTS:
        remaining = TARGET_NEW_ACCOUNTS - completed_in_session
        current_chunk = min(CHUNK_SIZE, remaining)
        slog(f"\n[*] Launching chunk of {current_chunk} accounts (Session progress: {completed_in_session}/{TARGET_NEW_ACCOUNTS})...")

        cleanup_runtime()

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
        try:
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
        except Exception as e:
            slog(f"[!] Subprocess error: {e}")

        current_db_count = get_current_db_count()
        chunk_added = max(0, current_db_count - (initial_db_count + completed_in_session))
        completed_in_session = max(0, current_db_count - initial_db_count)

        duration_so_far = str(timedelta(seconds=int((datetime.now() - start_time).total_seconds())))
        acc_file_total = get_accounts_file_count()
        slog(f"[+] Chunk cycle finished. Added {chunk_added} accounts (Total verified in DB: {completed_in_session}/{TARGET_NEW_ACCOUNTS}). Duration: {duration_so_far}")

        # Check milestone (every 50 accounts)
        milestone = (completed_in_session // 50) * 50
        if milestone > last_reported_milestone and milestone < TARGET_NEW_ACCOUNTS:
            last_reported_milestone = milestone
            send_discord_report(
                f"{completed_in_session}/{TARGET_NEW_ACCOUNTS} Akun Baru (Batch #{batch_number})",
                completed_in_session,
                TARGET_NEW_ACCOUNTS,
                duration_so_far,
                current_db_count,
                acc_file_total,
                is_final=False,
            )

        time.sleep(5)

    # 500 reached! Sync to Oracle
    final_duration = str(timedelta(seconds=int((datetime.now() - start_time).total_seconds())))
    slog("\n==================================================")
    slog(f"[+] Batch #{batch_number} hit target {completed_in_session}/{TARGET_NEW_ACCOUNTS}! Syncing to Oracle...")
    slog("==================================================")

    sync_ok, oracle_total = sync_batch_to_oracle()
    if not sync_ok:
        slog("[!] Sync to Oracle failed, retrying in 10s...")
        time.sleep(10)
        sync_ok, oracle_total = sync_batch_to_oracle()

    if sync_ok:
        archive_and_reset_staging()
        send_discord_report(
            f"Batch #{batch_number} Selesai",
            completed_in_session,
            TARGET_NEW_ACCOUNTS,
            final_duration,
            current_db_count,
            acc_file_total,
            oracle_total=oracle_total,
            is_final=True,
        )
    else:
        slog("[!] CRITICAL: Sync failed twice! Keeping staging files intact to prevent data loss.")


def main():
    slog("==================================================")
    slog("[*] Starting Continuous 500-Batch Grok Pipeline")
    slog("==================================================")

    batch_number = 1
    while True:
        try:
            run_single_500_batch(batch_number)
            batch_number += 1
            slog("\n[*] Cooling down 30s before next batch...")
            time.sleep(30)
        except Exception as e:
            slog(f"[!] Unhandled exception in batch loop: {e}")
            time.sleep(15)


if __name__ == "__main__":
    main()
