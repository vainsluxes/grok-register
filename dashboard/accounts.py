"""Local account files + cpa_auths helpers."""
from __future__ import annotations

import glob
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REG_DIR = Path(__file__).resolve().parents[1]
CPA_DIR = REG_DIR / "cpa_auths"


def list_account_files() -> List[Dict[str, Any]]:
    files = sorted(glob.glob(str(REG_DIR / "accounts_*.txt")), key=os.path.getmtime, reverse=True)
    out = []
    for f in files:
        p = Path(f)
        try:
            lines = [ln for ln in p.read_text(encoding="utf-8", errors="ignore").splitlines() if ln.strip()]
        except Exception:
            lines = []
        out.append(
            {
                "path": str(p),
                "name": p.name,
                "mtime": p.stat().st_mtime,
                "count": len(lines),
            }
        )
    return out


def parse_accounts_file(path: str) -> List[Dict[str, str]]:
    """Parse accounts file lines into {email,password,sso?}."""
    accounts = []
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            raw = line.strip()
            if not raw:
                continue
            if "|" in raw.split("----", 1)[0]:
                raw = raw.split("|", 1)[1]
            parts = raw.split("----")
            if len(parts) >= 2 and parts[0] and parts[1]:
                email = parts[0].strip()
                password = parts[1].strip()
                sso = parts[2].strip() if len(parts) >= 3 else ""
                if "@" in email:
                    accounts.append({"email": email, "password": password, "sso": sso})
    return accounts


def latest_accounts_path() -> Optional[str]:
    files = list_account_files()
    return files[0]["path"] if files else None


def list_cpa_auths() -> List[Dict[str, Any]]:
    files = sorted(glob.glob(str(CPA_DIR / "xai-*.json")), key=os.path.getmtime, reverse=True)
    out = []
    for f in files:
        p = Path(f)
        email = ""
        has_tokens = False
        expired = ""
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            email = d.get("email") or ""
            has_tokens = bool(d.get("access_token") and d.get("refresh_token"))
            expired = d.get("expired") or ""
        except Exception:
            base = p.name
            if base.startswith("xai-") and base.endswith(".json"):
                email = base[4:-5]
        out.append(
            {
                "path": str(p),
                "name": p.name,
                "email": email,
                "has_tokens": has_tokens,
                "expired": expired,
                "mtime": p.stat().st_mtime,
            }
        )
    return out


def read_cpa_auth(email: str) -> Optional[Dict[str, Any]]:
    safe = "".join(c if (c.isalnum() or c in "@._-") else "-" for c in email)
    path = CPA_DIR / f"xai-{safe}.json"
    if not path.exists():
        # try scan
        for item in list_cpa_auths():
            if item.get("email", "").lower() == email.lower():
                path = Path(item["path"])
                break
        else:
            return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def failed_cpa_log(limit: int = 50) -> List[Dict[str, str]]:
    path = CPA_DIR / "cpa_auth_failed.txt"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or "----" not in line:
            continue
        # strip optional leading "N|"
        if "|" in line.split("----", 1)[0]:
            line = line.split("|", 1)[1]
        parts = line.split("----")
        if len(parts) >= 2:
            rows.append(
                {
                    "email": parts[0],
                    "error": parts[1][:200],
                    "ts": parts[2] if len(parts) > 2 else "",
                }
            )
    return list(reversed(rows[-limit:]))


def local_overview() -> Dict[str, Any]:
    account_files = list_account_files()
    latest = account_files[0] if account_files else None
    latest_accounts = parse_accounts_file(latest["path"]) if latest else []
    cpa = list_cpa_auths()
    cpa_emails = {c["email"].lower() for c in cpa if c.get("email")}
    missing_cpa = [a["email"] for a in latest_accounts if a["email"].lower() not in cpa_emails]
    return {
        "reg_dir": str(REG_DIR),
        "cpa_dir": str(CPA_DIR),
        "account_files": len(account_files),
        "latest_accounts_file": latest["name"] if latest else None,
        "latest_accounts_count": len(latest_accounts),
        "cpa_auth_count": len(cpa),
        "latest_missing_cpa": len(missing_cpa),
        "failed_recent": failed_cpa_log(10),
    }
