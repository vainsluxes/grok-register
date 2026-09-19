"""Probe grok-cli connections: validate token + detect free-usage quota.

Uses:
1) 9Router POST /api/providers/{id}/test (local, cheap)
2) Optional live probe: refresh (if needed) + GET models / tiny chat against xAI
3) Writes testStatus/lastError back into 9Router SQLite
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from . import db as dbmod

NINE_BASE = os.environ.get("NINE_ROUTER_BASE", "http://127.0.0.1:20128").rstrip("/")
XAI_CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
XAI_TOKEN_URL = "https://auth.x.ai/oauth2/token"
XAI_MODELS_URLS = (
    "https://cli-chat-proxy.grok.com/v1/models",
    "https://api.x.ai/v1/models",
)
UA = "grok-cli/9router"


LogFn = Callable[[str], None]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _http_json(
    url: str,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    data: Optional[bytes] = None,
    timeout: float = 30.0,
) -> Dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers=headers or {},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "ignore")
            body: Any
            try:
                body = json.loads(raw) if raw else {}
            except Exception:
                body = {"raw": raw[:500]}
            return {"ok": True, "status": resp.status, "body": body, "error": None}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "ignore") if exc.fp else ""
        try:
            body = json.loads(raw) if raw else {}
        except Exception:
            body = {"raw": raw[:500]}
        return {"ok": False, "status": exc.code, "body": body, "error": raw[:400] or str(exc)}
    except Exception as exc:
        return {"ok": False, "status": None, "body": {}, "error": str(exc)}


def _load_connection_raw(conn_id: str) -> Optional[Dict[str, Any]]:
    conn = dbmod.connect()
    try:
        row = conn.execute(
            "SELECT id, provider, authType, name, email, data FROM providerConnections WHERE id=?",
            (conn_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    data = dbmod._parse_data(row["data"])
    return {
        "id": row["id"],
        "provider": row["provider"],
        "authType": row["authType"],
        "name": row["name"],
        "email": row["email"],
        "data": data,
        "accessToken": data.get("accessToken") or data.get("access_token") or "",
        "refreshToken": data.get("refreshToken") or data.get("refresh_token") or "",
    }


def _write_status(
    conn_id: str,
    *,
    test_status: str,
    last_error: Optional[str] = None,
    access_token: Optional[str] = None,
    refresh_token: Optional[str] = None,
    expires_at: Optional[str] = None,
    expires_in: Optional[int] = None,
) -> None:
    conn = dbmod.connect()
    try:
        row = conn.execute(
            "SELECT data FROM providerConnections WHERE id=?",
            (conn_id,),
        ).fetchone()
        if not row:
            return
        data = dbmod._parse_data(row["data"])
        data["testStatus"] = test_status
        if last_error:
            data["lastError"] = last_error[:500]
            data["lastErrorAt"] = _now()
        else:
            data["lastError"] = None
            data["lastErrorAt"] = None
            data.pop("errorCode", None)
            data["backoffLevel"] = 0
        if access_token:
            data["accessToken"] = access_token
        if refresh_token:
            data["refreshToken"] = refresh_token
        if expires_at:
            data["expiresAt"] = expires_at
        if expires_in is not None:
            data["expiresIn"] = expires_in
        conn.execute(
            "UPDATE providerConnections SET data=?, updatedAt=? WHERE id=?",
            (json.dumps(data), _now(), conn_id),
        )
        conn.commit()
    finally:
        conn.close()


def nine_router_test(conn_id: str) -> Dict[str, Any]:
    """Call 9Router's own test endpoint (often only checks token presence for grok-cli)."""
    return _http_json(
        f"{NINE_BASE}/api/providers/{conn_id}/test",
        method="POST",
        headers={"Content-Type": "application/json"},
        data=b"{}",
        timeout=45.0,
    )


def refresh_xai_token(refresh_token: str) -> Dict[str, Any]:
    body = urllib.parse.urlencode(
        {
            "grant_type": "refresh_token",
            "client_id": XAI_CLIENT_ID,
            "refresh_token": refresh_token,
        }
    ).encode()
    return _http_json(
        XAI_TOKEN_URL,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": UA,
            "Accept": "application/json",
        },
        data=body,
        timeout=25.0,
    )


def _classify_error_text(text: str) -> str:
    t = (text or "").lower()
    if "free-usage-exhausted" in t or "used all the included free usage" in t:
        return "quota_exhausted"
    if "invalid_grant" in t or "revoked" in t or "token invalid" in t:
        return "token_invalid_revoked"
    if "access denied" in t:
        return "token_invalid_revoked"
    if "429" in t or "rate" in t:
        return "rate_limited"
    return "error"


def probe_models(access_token: str) -> Dict[str, Any]:
    """Lightweight live check — list models with access token."""
    last: Dict[str, Any] = {"ok": False, "status": None, "body": {}, "error": "no endpoint tried"}
    for url in XAI_MODELS_URLS:
        last = _http_json(
            url,
            method="GET",
            headers={
                "Authorization": f"Bearer {access_token}",
                "User-Agent": UA,
                "Accept": "application/json",
            },
            timeout=25.0,
        )
        if last.get("ok"):
            return last
        # Prefer first meaningful non-network response
        if last.get("status"):
            return last
    return last


def probe_one(
    conn_id: str,
    *,
    live: bool = True,
    log: Optional[LogFn] = None,
) -> Dict[str, Any]:
    """Probe a single connection and persist status."""
    logger = log or (lambda _m: None)
    raw = _load_connection_raw(conn_id)
    if not raw:
        return {"id": conn_id, "ok": False, "error": "not found"}

    email = raw.get("email") or raw.get("name") or conn_id
    provider = raw.get("provider")
    result: Dict[str, Any] = {
        "id": conn_id,
        "email": email,
        "provider": provider,
        "ok": False,
        "bucket": "error",
        "testStatus": "error",
        "error": None,
        "nine_test": None,
        "live": None,
        "refreshed": False,
    }

    if provider not in ("grok-cli", "grok-web"):
        result["error"] = f"unsupported provider {provider}"
        return result

    # 1) 9Router native test (updates DB its own way; we still normalize after)
    nine = nine_router_test(conn_id)
    result["nine_test"] = {
        "http": nine.get("status"),
        "valid": (nine.get("body") or {}).get("valid") if isinstance(nine.get("body"), dict) else None,
        "error": (nine.get("body") or {}).get("error") if isinstance(nine.get("body"), dict) else nine.get("error"),
    }
    logger(f"[{email}] 9router test http={nine.get('status')} valid={result['nine_test']['valid']}")

    if provider == "grok-web":
        # cookie providers: trust 9router test more
        valid = bool(result["nine_test"]["valid"])
        if valid:
            _write_status(conn_id, test_status="active", last_error=None)
            result.update(ok=True, bucket="active", testStatus="active")
        else:
            err = result["nine_test"]["error"] or nine.get("error") or "test failed"
            bucket = "sso_invalid" if "sso" in str(err).lower() or "cookie" in str(err).lower() else "error"
            status = "error"
            _write_status(conn_id, test_status=status, last_error=str(err))
            result.update(ok=False, bucket=bucket, testStatus=status, error=str(err))
        return result

    # grok-cli
    access = raw.get("accessToken") or ""
    refresh = raw.get("refreshToken") or ""

    if not access and not refresh:
        _write_status(conn_id, test_status="error", last_error="No access/refresh token")
        result.update(ok=False, bucket="no_token", testStatus="error", error="No access/refresh token")
        return result

    if not live:
        # Non-live: only 9router test result
        if result["nine_test"]["valid"]:
            _write_status(conn_id, test_status="active", last_error=None)
            result.update(ok=True, bucket="active", testStatus="active")
        else:
            err = result["nine_test"]["error"] or "test failed"
            bucket = _classify_error_text(str(err))
            status = "unavailable" if bucket == "quota_exhausted" else "error"
            _write_status(conn_id, test_status=status, last_error=str(err))
            result.update(ok=False, bucket=bucket, testStatus=status, error=str(err))
        return result

    # 2) Live: refresh if possible, then models probe (detects quota / revoked better)
    if refresh:
        ref = refresh_xai_token(refresh)
        result["refresh"] = {"http": ref.get("status"), "ok": ref.get("ok")}
        if ref.get("ok") and isinstance(ref.get("body"), dict) and ref["body"].get("access_token"):
            access = ref["body"]["access_token"]
            refresh = ref["body"].get("refresh_token") or refresh
            expires_in = ref["body"].get("expires_in")
            expires_at = None
            if expires_in:
                expires_at = datetime.fromtimestamp(
                    time.time() + int(expires_in), tz=timezone.utc
                ).strftime("%Y-%m-%dT%H:%M:%SZ")
            result["refreshed"] = True
            logger(f"[{email}] refresh OK")
            # persist refreshed tokens early
            _write_status(
                conn_id,
                test_status=raw["data"].get("testStatus") or "unknown",
                last_error=raw["data"].get("lastError"),
                access_token=access,
                refresh_token=refresh,
                expires_at=expires_at,
                expires_in=int(expires_in) if expires_in else None,
            )
        else:
            err_body = ref.get("body") if isinstance(ref.get("body"), dict) else {}
            err_txt = (
                (err_body.get("error_description") if isinstance(err_body, dict) else None)
                or (err_body.get("error") if isinstance(err_body, dict) else None)
                or ref.get("error")
                or f"refresh http {ref.get('status')}"
            )
            err_txt = str(err_txt)
            bucket = _classify_error_text(err_txt + " " + str(ref.get("body")))
            # If refresh fails hard, token is dead — no need models call
            if bucket == "token_invalid_revoked" or ref.get("status") in (400, 401, 403):
                _write_status(conn_id, test_status="error", last_error=err_txt)
                result.update(ok=False, bucket=bucket, testStatus="error", error=err_txt)
                logger(f"[{email}] refresh FAIL: {err_txt[:120]}")
                return result
            logger(f"[{email}] refresh soft-fail, try existing access token: {err_txt[:80]}")

    if not access:
        _write_status(conn_id, test_status="error", last_error="No access token after refresh")
        result.update(ok=False, bucket="no_token", testStatus="error", error="No access token")
        return result

    live_res = probe_models(access)
    result["live"] = {
        "http": live_res.get("status"),
        "ok": live_res.get("ok"),
        "error": live_res.get("error"),
    }
    body = live_res.get("body") if isinstance(live_res.get("body"), dict) else {}
    body_txt = json.dumps(body) if body else (live_res.get("error") or "")

    if live_res.get("ok"):
        _write_status(conn_id, test_status="active", last_error=None, access_token=access)
        result.update(ok=True, bucket="active", testStatus="active", error=None)
        logger(f"[{email}] LIVE OK → active")
        return result

    bucket = _classify_error_text(body_txt + " " + str(live_res.get("error") or ""))
    err_msg = None
    if isinstance(body, dict):
        err_msg = body.get("error") or body.get("message") or body.get("code")
        if isinstance(err_msg, dict):
            err_msg = err_msg.get("message") or err_msg.get("error") or json.dumps(err_msg)
    err_msg = str(err_msg or live_res.get("error") or f"HTTP {live_res.get('status')}")

    if bucket == "quota_exhausted":
        # Keep token, mark unavailable/quota
        _write_status(
            conn_id,
            test_status="unavailable",
            last_error=f"[429]: {err_msg}" if "free-usage" not in err_msg else err_msg,
            access_token=access,
        )
        result.update(ok=False, bucket="quota_exhausted", testStatus="unavailable", error=err_msg)
        logger(f"[{email}] QUOTA exhausted")
        return result

    if bucket == "token_invalid_revoked":
        _write_status(conn_id, test_status="error", last_error=err_msg, access_token=access)
        result.update(ok=False, bucket=bucket, testStatus="error", error=err_msg)
        logger(f"[{email}] TOKEN invalid/revoked")
        return result

    # rate limit or generic — mark unavailable but keep token
    status = "unavailable" if live_res.get("status") == 429 else "error"
    _write_status(conn_id, test_status=status, last_error=err_msg, access_token=access)
    result.update(ok=False, bucket=bucket if bucket != "error" else status, testStatus=status, error=err_msg)
    logger(f"[{email}] LIVE fail status={status}: {err_msg[:120]}")
    return result


def select_targets(
    provider: str = "grok-cli",
    mode: str = "auto",  # auto | unknown | inactive | all | ids
    ids: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    rows = dbmod.list_connections(provider)
    if mode == "ids" and ids:
        want = set(ids)
        return [r for r in rows if r["id"] in want]
    if mode == "unknown":
        return [
            r
            for r in rows
            if r["bucket"] in ("unknown", "none")
            or r["testStatus"] in ("unknown", "none", "")
        ]
    if mode == "inactive":
        return [r for r in rows if not r["active"]]
    if mode == "all":
        return rows
    if mode == "auto":
        # Prefer unknown; if none, fall back to inactive; if none, all.
        unknown = [
            r
            for r in rows
            if r["bucket"] in ("unknown", "none")
            or r["testStatus"] in ("unknown", "none", "")
        ]
        if unknown:
            return unknown
        inactive = [r for r in rows if not r["active"]]
        if inactive:
            return inactive
        return rows
    # default auto-like unknown
    return [
        r
        for r in rows
        if r["bucket"] in ("unknown", "none")
        or r["testStatus"] in ("unknown", "none", "")
    ]


def probe_batch(
    *,
    provider: str = "grok-cli",
    mode: str = "auto",
    ids: Optional[List[str]] = None,
    live: bool = True,
    sleep_sec: float = 1.5,
    limit: Optional[int] = None,
    log: Optional[LogFn] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    logger = log or (lambda _m: None)
    effective_mode = mode
    targets = select_targets(provider=provider, mode=mode, ids=ids)

    # Friendly fallback: "unknown" with 0 targets → try inactive once.
    if mode == "unknown" and not targets:
        inactive = select_targets(provider=provider, mode="inactive", ids=None)
        if inactive:
            logger(
                f"mode=unknown targeted=0 → auto-fallback to inactive ({len(inactive)} akun)"
            )
            targets = inactive
            effective_mode = "inactive(fallback)"
        else:
            logger(
                "mode=unknown targeted=0 dan tidak ada inactive. "
                "Semua koneksi sudah active, atau provider kosong. "
                "Pakai mode 'all' / 'all inactive' / pilih baris + Probe selected."
            )

    if limit is not None:
        targets = targets[: max(0, int(limit))]

    logger(
        f"probe start provider={provider} mode={effective_mode} live={live} "
        f"targets={len(targets)} sleep={sleep_sec}"
    )
    if not targets:
        logger("[done] nothing to probe")
        return {
            "ok": True,
            "provider": provider,
            "mode": effective_mode,
            "live": live,
            "targeted": 0,
            "scanned": 0,
            "counts": {
                "active": 0,
                "quota_exhausted": 0,
                "token_invalid_revoked": 0,
                "error": 0,
                "other": 0,
            },
            "results": [],
            "message": "no targets",
        }

    results = []
    counts = {
        "active": 0,
        "quota_exhausted": 0,
        "token_invalid_revoked": 0,
        "error": 0,
        "other": 0,
    }
    for i, row in enumerate(targets):
        if should_cancel and should_cancel():
            logger("probe cancelled")
            break
        logger(f"--- [{i+1}/{len(targets)}] {row.get('email') or row.get('id')} bucket={row.get('bucket')} ---")
        r = probe_one(row["id"], live=live, log=logger)
        results.append(r)
        b = r.get("bucket") or "other"
        if b in counts:
            counts[b] += 1
        else:
            counts["other"] += 1
        if i + 1 < len(targets) and sleep_sec > 0:
            time.sleep(float(sleep_sec))

    return {
        "ok": True,
        "provider": provider,
        "mode": effective_mode,
        "live": live,
        "targeted": len(targets),
        "scanned": len(results),
        "counts": counts,
        "results": results,
    }
