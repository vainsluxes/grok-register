"""9Router SQLite helpers for Grok providers."""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

DB_PATH = os.path.expanduser("~/.9router/db/data.sqlite")
GROK_PROVIDERS = ("grok-cli", "grok-web")


def connect() -> sqlite3.Connection:
    if not os.path.exists(DB_PATH):
        raise FileNotFoundError(f"9Router DB not found: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _parse_data(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def classify_connection(provider: str, data: Dict[str, Any]) -> str:
    """Normalize status bucket for dashboard."""
    st = str(data.get("testStatus") or "none").lower()
    le = str(data.get("lastError") or "")
    le_l = le.lower()

    if provider == "grok-cli":
        at = data.get("accessToken") or data.get("access_token") or ""
        rt = data.get("refreshToken") or data.get("refresh_token") or ""
        if not at and not rt:
            return "no_token"
        if "token invalid" in le_l or "revoked" in le_l or "invalid_grant" in le_l:
            return "token_invalid_revoked"
        if "free-usage-exhausted" in le_l:
            return "quota_exhausted"
        if st == "active":
            return "active"
        if st in ("unavailable", "error", "unknown", "none"):
            return st
        return f"other_{st}"

    # grok-web
    key = data.get("apiKey") or ""
    if not key:
        return "no_token"
    if "invalid sso" in le_l:
        return "sso_invalid"
    if st == "active":
        return "active"
    if st in ("error", "unavailable", "unknown", "none"):
        return st
    return f"other_{st}"


def is_active_bucket(bucket: str) -> bool:
    return bucket == "active"


def list_connections(provider: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = connect()
    try:
        if provider:
            rows = conn.execute(
                "SELECT id, provider, authType, name, email, priority, isActive, data, createdAt, updatedAt "
                "FROM providerConnections WHERE provider=? ORDER BY updatedAt DESC",
                (provider,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, provider, authType, name, email, priority, isActive, data, createdAt, updatedAt "
                "FROM providerConnections WHERE provider IN ('grok-cli','grok-web') "
                "ORDER BY provider, updatedAt DESC"
            ).fetchall()
    finally:
        conn.close()

    out: List[Dict[str, Any]] = []
    for r in rows:
        data = _parse_data(r["data"])
        bucket = classify_connection(r["provider"], data)
        email = (r["email"] or data.get("email") or (data.get("providerSpecificData") or {}).get("email") or r["name"] or "")
        out.append(
            {
                "id": r["id"],
                "provider": r["provider"],
                "authType": r["authType"],
                "name": r["name"],
                "email": email,
                "priority": r["priority"],
                "isActive": bool(r["isActive"]),
                "testStatus": data.get("testStatus") or "none",
                "bucket": bucket,
                "active": is_active_bucket(bucket),
                "lastError": (data.get("lastError") or "")[:300] or None,
                "lastErrorAt": data.get("lastErrorAt"),
                "hasToken": bool(
                    data.get("accessToken")
                    or data.get("access_token")
                    or data.get("apiKey")
                    or data.get("refreshToken")
                ),
                "createdAt": r["createdAt"],
                "updatedAt": r["updatedAt"],
                "expiresAt": data.get("expiresAt") or data.get("expired"),
            }
        )
    return out


def summary() -> Dict[str, Any]:
    rows = list_connections()
    by_provider: Dict[str, Counter] = {"grok-cli": Counter(), "grok-web": Counter()}
    for row in rows:
        by_provider.setdefault(row["provider"], Counter())[row["bucket"]] += 1

    result = {
        "db_path": DB_PATH,
        "db_exists": os.path.exists(DB_PATH),
        "providers": {},
        "totals": {"connections": len(rows)},
    }
    for prov in GROK_PROVIDERS:
        c = by_provider.get(prov, Counter())
        total = sum(c.values())
        active = c.get("active", 0)
        result["providers"][prov] = {
            "total": total,
            "active": active,
            "inactive": total - active,
            "buckets": dict(c),
        }
    return result


def delete_by_ids(ids: List[str]) -> int:
    if not ids:
        return 0
    conn = connect()
    try:
        cur = conn.cursor()
        deleted = 0
        for i in ids:
            cur.execute("DELETE FROM providerConnections WHERE id=?", (i,))
            deleted += cur.rowcount
        conn.commit()
        return deleted
    finally:
        conn.close()


def delete_by_filter(
    provider: Optional[str] = None,
    buckets: Optional[List[str]] = None,
    inactive_only: bool = False,
    all_provider: bool = False,
) -> Tuple[int, List[str]]:
    """Delete connections matching filter. Returns (count, emails)."""
    rows = list_connections(provider)
    targets = []
    for row in rows:
        if provider and row["provider"] != provider:
            continue
        if all_provider and provider and row["provider"] == provider:
            targets.append(row)
            continue
        if inactive_only and row["active"]:
            continue
        if buckets and row["bucket"] not in buckets:
            continue
        if not all_provider and not inactive_only and not buckets:
            continue
        targets.append(row)

    # de-dupe
    seen = set()
    uniq = []
    for t in targets:
        if t["id"] in seen:
            continue
        seen.add(t["id"])
        uniq.append(t)

    ids = [t["id"] for t in uniq]
    emails = [t["email"] or t["name"] or t["id"] for t in uniq]
    n = delete_by_ids(ids)
    return n, emails


def upsert_grok_cli(email: str, token_data: Dict[str, Any]) -> str:
    """Insert or update grok-cli connection. Returns 'inserted'|'updated'."""
    email = str(email or "").strip()
    if not email:
        raise ValueError("email required")

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
    user_id = token_data.get("user_id") or token_data.get("userId") or token_data.get("sub") or ""

    if not access_token:
        raise ValueError("access_token missing")

    data = {
        "displayName": email.split("@")[0],
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
            "userId": user_id,
            "hasGrokCodeAccess": True,
            "subscriptionTier": None,
        },
        "lastError": None,
        "lastErrorAt": None,
    }

    now = datetime.now(timezone.utc).isoformat()
    conn = connect()
    try:
        cur = conn.cursor()
        existing = cur.execute(
            "SELECT id FROM providerConnections WHERE provider='grok-cli' AND (email=? OR name=?)",
            (email, email),
        ).fetchone()
        if existing:
            cur.execute(
                """UPDATE providerConnections
                   SET authType=?, name=?, email=?, priority=?, isActive=?, data=?, updatedAt=?
                   WHERE id=?""",
                ("oauth", email, email, 1, 1, json.dumps(data), now, existing["id"]),
            )
            conn.commit()
            return "updated"
        conn_id = str(uuid.uuid4())
        cur.execute(
            """INSERT INTO providerConnections
            (id, provider, authType, name, email, priority, isActive, data, createdAt, updatedAt)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (conn_id, "grok-cli", "oauth", email, email, 1, 1, json.dumps(data), now, now),
        )
        conn.commit()
        return "inserted"
    finally:
        conn.close()


def upsert_grok_web(email: str, sso_token: str) -> str:
    email = str(email or "").strip()
    sso_token = str(sso_token or "").strip()
    if not email or not sso_token or sso_token == "N/A":
        raise ValueError("email and sso_token required")

    data = {
        "apiKey": sso_token,
        "testStatus": "unknown",
        "providerSpecificData": {
            "connectionProxyEnabled": False,
            "connectionProxyUrl": "",
            "connectionNoProxy": "",
        },
    }
    now = datetime.now(timezone.utc).isoformat()
    conn = connect()
    try:
        cur = conn.cursor()
        existing = cur.execute(
            "SELECT id FROM providerConnections WHERE provider='grok-web' AND (email=? OR name=?)",
            (email, email),
        ).fetchone()
        if existing:
            cur.execute(
                """UPDATE providerConnections
                   SET authType=?, name=?, email=?, priority=?, isActive=?, data=?, updatedAt=?
                   WHERE id=?""",
                ("cookie", email, email, 1, 1, json.dumps(data), now, existing["id"]),
            )
            conn.commit()
            return "updated"
        conn_id = str(uuid.uuid4())
        cur.execute(
            """INSERT INTO providerConnections
            (id, provider, authType, name, email, priority, isActive, data, createdAt, updatedAt)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (conn_id, "grok-web", "cookie", email, email, 1, 1, json.dumps(data), now, now),
        )
        conn.commit()
        return "inserted"
    finally:
        conn.close()
