"""Shared single-account registration and batch execution flow for GUI and CLI."""
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Tuple
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone


def _push_to_9router_grokcli(email, cpa_result, callbacks):
    """Push OAuth credentials to 9Router grok-cli provider.

    Prefer in-memory tokens from mint result. Fall back to reading CPA JSON
    only when tokens are missing but a path exists (legacy / save_auth_file).
    """
    if not cpa_result or not cpa_result.get("ok"):
        return
    if cpa_result.get("push_9router") is False:
        callbacks.log(f"[*] 9Router: push disabled for {email}, skipping")
        return

    db_path = os.path.expanduser("~/.9router/db/data.sqlite")
    if not os.path.exists(db_path):
        callbacks.log("[!] 9Router: DB not found, skipping grok-cli push")
        return

    access_token = (
        cpa_result.get("access_token")
        or cpa_result.get("accessToken")
        or ""
    )
    refresh_token = (
        cpa_result.get("refresh_token")
        or cpa_result.get("refreshToken")
        or ""
    )
    id_token = cpa_result.get("id_token") or cpa_result.get("idToken") or ""
    expires_at = cpa_result.get("expired") or cpa_result.get("expires_at") or cpa_result.get("expiresAt") or ""
    expires_in = cpa_result.get("expires_in") or cpa_result.get("expiresIn") or 21600
    user_id = cpa_result.get("sub") or cpa_result.get("user_id") or ""

    # Nested payload from mint_and_export
    payload = cpa_result.get("payload")
    if isinstance(payload, dict):
        access_token = access_token or payload.get("access_token") or ""
        refresh_token = refresh_token or payload.get("refresh_token") or ""
        id_token = id_token or payload.get("id_token") or ""
        expires_at = expires_at or payload.get("expired") or ""
        expires_in = expires_in or payload.get("expires_in") or 21600
        user_id = user_id or payload.get("sub") or ""

    # Legacy fallback: read local CPA file if still no access token
    if not access_token:
        auth_path = cpa_result.get("hotload_path") or cpa_result.get("path") or ""
        if auth_path and os.path.exists(auth_path):
            try:
                with open(auth_path, "r", encoding="utf-8") as f:
                    auth = json.load(f)
                access_token = auth.get("access_token", "") or access_token
                refresh_token = auth.get("refresh_token", "") or refresh_token
                id_token = auth.get("id_token", "") or id_token
                expires_at = auth.get("expired") or auth.get("expires_at") or expires_at
                expires_in = auth.get("expires_in") or expires_in
                user_id = auth.get("sub") or user_id
            except Exception as exc:
                callbacks.log(f"[!] 9Router: failed to read CPA file: {exc}")
        else:
            callbacks.log("[!] 9Router: no tokens and no CPA auth file, skipping")
            return

    if not access_token:
        callbacks.log("[!] 9Router: no access_token, skipping")
        return

    email = str(email or cpa_result.get("email") or "").strip()
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    existing = cursor.execute(
        "SELECT id FROM providerConnections WHERE provider='grok-cli' AND (email=? OR name=?)",
        (email, email),
    ).fetchone()

    now = datetime.now(timezone.utc).isoformat()
    data = {
        "displayName": email.split("@")[0] if email else "",
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

    if existing:
        cursor.execute(
            """UPDATE providerConnections
               SET authType=?, name=?, email=?, priority=?, isActive=?, data=?, updatedAt=?
               WHERE id=?""",
            ("oauth", email, email, 1, 1, json.dumps(data), now, existing[0]),
        )
        conn.commit()
        conn.close()
        callbacks.log(f"[+] 9Router grok-cli updated: {email}")
        return

    conn_id = str(uuid.uuid4())
    cursor.execute(
        """INSERT INTO providerConnections
        (id, provider, authType, name, email, priority, isActive, data, createdAt, updatedAt)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (conn_id, "grok-cli", "oauth", email, email, 1, 1, json.dumps(data), now, now),
    )
    conn.commit()
    conn.close()
    callbacks.log(f"[+] 9Router grok-cli: {email}")


def _push_to_9router_grokweb(email, sso_token, callbacks):
    """Push SSO token directly to 9Router grok-web provider."""
    if not sso_token or sso_token == "N/A":
        return
        
    db_path = os.path.expanduser("~/.9router/db/data.sqlite")
    if not os.path.exists(db_path):
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM providerConnections WHERE provider='grok-web' AND name=?", (email,))
    if cursor.fetchone():
        conn.close()
        callbacks.log(f"[*] 9Router grok-web: {email} already exists, skipping")
        return

    now = datetime.now(timezone.utc).isoformat()
    conn_id = str(uuid.uuid4())
    
    # In 9Router, the sso token for cookie-based providers (like grok-web)
    # is stored inside the 'data' JSON string under the 'apiKey' key.
    # Structure: {"apiKey": "sso_token", "connectionProxyEnabled": false, ...}
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
    callbacks.log(f"[+] 9Router grok-web: {email}")


@dataclass
class RegistrationCallbacks:
    log: Callable[[str], None]
    cancelled: Callable[[], bool]


@dataclass
class RegistrationOperations:
    start_browser: Callable[[], None]
    restart_browser: Callable[[], None]
    browser_missing: Callable[[], bool]
    open_signup_page: Callable[[], None]
    fill_email_and_submit: Callable[[], Tuple[str, str]]
    save_mail_credential: Callable[[str, str], bool]
    fill_code_and_submit: Callable[[str, str], str]
    fill_profile_and_submit: Callable[[], Dict[str, Any]]
    wait_for_sso_cookie: Callable[[], str]
    enable_nsfw: Callable[[str], Tuple[bool, str]]
    persist_account_line: Callable[[str, str, str], None]
    queue_unsaved_result: Callable[[Dict[str, Any], str], bool]
    add_tokens: Callable[[str, str], Dict[str, Dict[str, Any]]]
    export_cpa: Callable[[str, str, str], Dict[str, Any]]
    cleanup: Callable[[str], None]
    sleep: Callable[[float], None]
    cancelled_exception: type
    retry_exception: type


@dataclass
class RegistrationResult:
    ok: bool
    email: str = ""
    password: str = ""
    sso: str = ""
    profile: Dict[str, Any] = field(default_factory=dict)
    error: str = ""
    retryable: bool = False


@dataclass
class OutputResult:
    registered: bool
    saved: bool
    pending_saved: bool = False
    save_error: str = ""
    pools: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    cpa: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RegistrationSettings:
    count: int
    enable_nsfw: bool = True
    max_mail_retry: int = 3
    max_slot_retry: int = 3
    cleanup_interval: int = 5


@dataclass
class BatchResult:
    success_count: int = 0
    fail_count: int = 0
    processed_count: int = 0
    registered_unsaved_count: int = 0
    postprocess_warning_count: int = 0
    cancelled: bool = False
    results: list = field(default_factory=list)


def register_one_account(callbacks, ops, enable_nsfw=True, max_mail_retry=3):
    email = ""
    dev_token = ""
    code = ""
    mail_ok = False
    for mail_try in range(1, max_mail_retry + 1):
        if callbacks.cancelled():
            raise ops.cancelled_exception()
        callbacks.log(f"[*] 1. Open signup page (attempt {mail_try}/{max_mail_retry})")
        ops.open_signup_page()
        callbacks.log("[*] 2. Create email and submit")
        email, dev_token = ops.fill_email_and_submit()
        callbacks.log(f"[*] Email: {email}")
        callbacks.log(f"[Debug] Email credential (jwt): {dev_token}")
        if not ops.save_mail_credential(email, dev_token):
            callbacks.log("[!] Email credential save failed, registration continues, exception recorded")
        callbacks.log("[*] 3. Fetch verification code")
        try:
            code = ops.fill_code_and_submit(email, dev_token)
            mail_ok = True
            break
        except Exception as exc:
            message = str(exc)
            if ("未收到验证码" in message or "验证码" in message) and mail_try < max_mail_retry:
                callbacks.log(f"[!] No verification code for this email, switching to new email: {message}")
                ops.restart_browser()
                ops.sleep(1)
                continue
            raise
    if not mail_ok:
        raise RuntimeError("verification code stage failed, max retries reached")
    callbacks.log(f"[*] Verification code: {code}")
    callbacks.log("[*] 4. Fill profile")
    profile = ops.fill_profile_and_submit()
    callbacks.log(f"[*] Profile filled: {profile.get('given_name')} {profile.get('family_name')}")
    callbacks.log("[*] 5. Waiting for sso cookie")
    sso = ops.wait_for_sso_cookie()
    if enable_nsfw:
        callbacks.log("[*] 6. Enabling NSFW")
        try:
            nsfw_ok, nsfw_msg = ops.enable_nsfw(sso)
            if nsfw_ok:
                callbacks.log(f"[+] NSFW enabled successfully: {nsfw_msg}")
            else:
                callbacks.log(f"[!] NSFW not enabled, continuing to save account: {nsfw_msg}")
        except Exception as exc:
            callbacks.log(f"[!] NSFW enable error, continuing to save account: {exc}")
    return RegistrationResult(
        ok=True,
        email=email,
        password=str(profile.get("password") or ""),
        sso=sso,
        profile=profile,
    )


def persist_account_result(result, callbacks, ops):
    try:
        ops.persist_account_line(result.email, result.password, result.sso)
        saved = True
        save_error = ""
        pending_saved = False
    except Exception as exc:
        saved = False
        save_error = str(exc)
        try:
            pending_saved = bool(
                ops.queue_unsaved_result(
                    {
                        "email": result.email,
                        "password": result.password,
                        "sso": result.sso,
                        "profile": result.profile,
                    },
                    save_error,
                )
            )
        except Exception as pending_exc:
            pending_saved = False
            callbacks.log(f"[!] pending queue write error: {pending_exc}")
        callbacks.log(f"[!] Account registered but main result file save failed: {save_error}")
        if pending_saved:
            callbacks.log("[!] Unsaved account written to pending queue, awaiting manual retry")
        else:
            callbacks.log("[!] Pending queue write also failed, please copy account info immediately")

    try:
        pools = ops.add_tokens(result.sso, result.email)
        if not isinstance(pools, dict):
            raise TypeError("token pool result must be a dict")
    except Exception as exc:
        callbacks.log(f"[!] Token pool post-processing error, account result preserved: {exc}")
        pools = {
            "internal": {
                "enabled": True,
                "ok": False,
                "error": str(exc),
            }
        }
    for name, state in pools.items():
        if isinstance(state, dict) and state.get("enabled") and not state.get("ok"):
            callbacks.log(f"[!] grok2api {name} pool add failed: {state.get('error')}")

    try:
        cpa = ops.export_cpa(result.email, result.password, result.sso)
        if not isinstance(cpa, dict):
            raise TypeError("CPA result must be a dict")
    except Exception as exc:
        callbacks.log(f"[!] CPA export post-processing error, account result preserved: {exc}")
        cpa = {"ok": False, "skipped": False, "error": str(exc)}

    try:
        _push_to_9router_grokcli(result.email, cpa, callbacks)
    except Exception as exc:
        callbacks.log(f"[!] 9Router grok-cli push error: {exc}")

    try:
        _push_to_9router_grokweb(result.email, result.sso, callbacks)
    except Exception as exc:
        callbacks.log(f"[!] 9Router grok-web push error: {exc}")

    return OutputResult(
        registered=True,
        saved=saved,
        pending_saved=pending_saved,
        save_error=save_error,
        pools=pools,
        cpa=cpa,
    )


def _notify_observer(observer, result, account, output, callbacks):
    try:
        observer(result, account, output)
    except Exception as exc:
        callbacks.log(f"[Debug] observer execution failed: {exc}")


def _run_cleanup_safely(ops, callbacks, reason):
    try:
        ops.cleanup(reason)
        return True
    except Exception as exc:
        callbacks.log(f"[!] Cleanup failed, ignored and does not affect account stats: {reason}: {exc}")
        return False


def _prepare_next_account(result, settings, callbacks, ops):
    if result.processed_count >= settings.count:
        return False
    if callbacks.cancelled():
        result.cancelled = True
        return False
    try:
        if ops.browser_missing():
            ops.start_browser()
        else:
            ops.restart_browser()
        ops.sleep(1)
        return True
    except ops.cancelled_exception:
        result.cancelled = True
        callbacks.log("[!] Stopped during inter-account preparation")
        return False


def run_batch(count, callbacks, observer, ops, enable_nsfw=True, cleanup_interval=5,
              max_slot_retry=3, max_mail_retry=3, settings=None):
    if settings is None:
        settings = RegistrationSettings(
            count=int(count),
            enable_nsfw=bool(enable_nsfw),
            cleanup_interval=int(cleanup_interval),
            max_slot_retry=int(max_slot_retry),
            max_mail_retry=int(max_mail_retry),
        )
    result = BatchResult()
    retry_count_for_slot = 0
    last_cleanup_success_count = 0
    try:
        ops.start_browser()
        callbacks.log("[*] Browser started")
        while result.processed_count < settings.count:
            if callbacks.cancelled():
                result.cancelled = True
                break
            callbacks.log(f"--- Starting account {result.processed_count + 1}/{settings.count} ---")
            account = None
            output = None
            continue_batch = True
            try:
                account = register_one_account(
                    callbacks,
                    ops,
                    enable_nsfw=settings.enable_nsfw,
                    max_mail_retry=settings.max_mail_retry,
                )
                output = persist_account_result(account, callbacks, ops)
                result.results.append({"registration": account, "output": output})
                retry_count_for_slot = 0
                result.processed_count += 1
                if output.saved:
                    result.success_count += 1
                    callbacks.log(f"[+] Registration and save succeeded: {account.email}")
                    if (
                        settings.cleanup_interval > 0
                        and result.success_count % settings.cleanup_interval == 0
                        and result.success_count != last_cleanup_success_count
                        and result.processed_count < settings.count
                    ):
                        _run_cleanup_safely(
                            ops,
                            callbacks,
                            f"Successfully registered {result.success_count} accounts, running periodic cleanup",
                        )
                        last_cleanup_success_count = result.success_count
                else:
                    result.fail_count += 1
                    result.registered_unsaved_count += 1
                    callbacks.log(f"[-] Registration succeeded but persistence incomplete: {account.email}")
                pool_warning = any(
                    isinstance(state, dict) and state.get("enabled") and not state.get("ok")
                    for state in output.pools.values()
                )
                cpa_warning = bool(
                    output.cpa
                    and not output.cpa.get("skipped")
                    and (
                        not output.cpa.get("ok")
                        or output.cpa.get("warning")
                        or output.cpa.get("cpa_copy_error")
                    )
                )
                if pool_warning or cpa_warning:
                    result.postprocess_warning_count += 1
            except ops.cancelled_exception:
                result.cancelled = True
                callbacks.log("[!] Registration stopped")
                continue_batch = False
            except ops.retry_exception as exc:
                retry_count_for_slot += 1
                if retry_count_for_slot <= settings.max_slot_retry:
                    callbacks.log(
                        f"[!] Current account flow stuck, retrying {retry_count_for_slot}/{settings.max_slot_retry}: {exc}"
                    )
                else:
                    result.fail_count += 1
                    result.processed_count += 1
                    retry_count_for_slot = 0
                    callbacks.log(f"[-] Current account reached max retries, skipping: {exc}")
            except Exception as exc:
                result.fail_count += 1
                result.processed_count += 1
                retry_count_for_slot = 0
                callbacks.log(f"[-] Registration failed: {exc}")
            finally:
                _notify_observer(observer, result, account, output, callbacks)

            if not continue_batch or result.cancelled:
                break
            if not _prepare_next_account(result, settings, callbacks, ops):
                break
    finally:
        _run_cleanup_safely(ops, callbacks, "task finished")
    return result

