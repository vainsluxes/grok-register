"""负责账号结果、pending 恢复以及 grok2api token 池的安全持久化。"""
import json
import os
import tempfile
import time
from contextlib import ExitStack
from datetime import datetime, timezone

from filelock import FileLock


def append_account_line(path, email, password, sso):
    # Check deduplication if target file exists
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as check_h:
                for line in check_h:
                    if line.startswith(f"{email}----"):
                        return  # Skip duplicate entry
        except Exception:
            pass
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"{email}----{password}----{sso}\n")
        handle.flush()
        os.fsync(handle.fileno())


def save_mail_credential(base_dir, email, credential):
    path = os.path.join(base_dir, "mail_credentials.txt")
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"{email}\t{credential}\n")
        handle.flush()
        os.fsync(handle.fileno())
    return True


def queue_unsaved_account(path, payload, error):
    pending_path = path + ".pending.jsonl"
    record = dict(payload)
    record["save_error"] = str(error)
    record["queued_at"] = datetime.now(timezone.utc).isoformat()
    with open(pending_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.chmod(pending_path, 0o600)
    except Exception:
        pass
    return True


def _existing_account_keys(target_path):
    keys = set()
    if not os.path.isfile(target_path):
        return keys
    with open(target_path, "r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            parts = raw_line.rstrip("\n").split("----", 2)
            if len(parts) == 3:
                keys.add((parts[0].strip(), parts[2].strip()))
    return keys


def retry_pending_file(pending_path, output_path=None, log_callback=None):
    logger = log_callback or (lambda message: None)
    pending_path = os.path.realpath(os.path.abspath(os.path.expanduser(str(pending_path))))
    if not os.path.isfile(pending_path):
        raise FileNotFoundError(f"Pending file does not exist: {pending_path}")
    suffix = ".pending.jsonl"
    if output_path:
        target_path = os.path.realpath(os.path.abspath(os.path.expanduser(str(output_path))))
    elif pending_path.endswith(suffix):
        target_path = os.path.realpath(pending_path[:-len(suffix)])
    else:
        target_path = os.path.realpath(pending_path + ".recovered.txt")
    if os.path.normcase(pending_path) == os.path.normcase(target_path):
        raise ValueError("Pending input and output files cannot be the same file")

    lock_paths = sorted(
        {pending_path + ".lock", target_path + ".lock"},
        key=lambda value: os.path.normcase(os.path.abspath(value)),
    )
    with ExitStack() as stack:
        for lock_path in lock_paths:
            stack.enter_context(FileLock(lock_path, timeout=30))
        if not os.path.isfile(pending_path):
            return {"restored": 0, "remaining": 0, "output_path": target_path}
        with open(pending_path, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
        existing = _existing_account_keys(target_path)
        unresolved = []
        restored = 0
        for line_number, raw_line in enumerate(lines, 1):
            stripped = raw_line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
                if not isinstance(record, dict):
                    raise ValueError("record must be a JSON object")
                email = str(record.get("email") or "").strip()
                password = str(record.get("password") or "")
                sso = str(record.get("sso") or "").strip()
                if not email or not sso:
                    raise ValueError("record missing email or sso")
                key = (email, sso)
                if key not in existing:
                    append_account_line(target_path, email, password, sso)
                    existing.add(key)
                restored += 1
                logger(f"[+] Restored pending account: {email}")
            except Exception as exc:
                unresolved.append(raw_line if raw_line.endswith("\n") else raw_line + "\n")
                logger(f"[!] Failed to restore pending line {line_number}: {exc}")

        directory = os.path.dirname(pending_path) or "."
        fd, temp_path = tempfile.mkstemp(prefix=".pending-retry-", suffix=".jsonl.tmp", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.writelines(unresolved)
                handle.flush()
                os.fsync(handle.fileno())
            if unresolved:
                os.replace(temp_path, pending_path)
                temp_path = None
                try:
                    os.chmod(pending_path, 0o600)
                except Exception:
                    pass
            else:
                os.unlink(temp_path)
                temp_path = None
                try:
                    os.unlink(pending_path)
                except FileNotFoundError:
                    pass
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)
        return {"restored": restored, "remaining": len(unresolved), "output_path": target_path}


# Token-pool runtime dependencies are injected by the application adapter.
config = {}
_http_get = None
_http_post = None
_log_exception = None
_remote_compat_error = RuntimeError
_remote_request_error = RuntimeError


def configure_token_runtime(config_ref, http_get, http_post, log_exception,
                            compatibility_error=RuntimeError, request_error=RuntimeError):
    global config, _http_get, _http_post, _log_exception
    global _remote_compat_error, _remote_request_error
    config = config_ref
    _http_get = http_get
    _http_post = http_post
    _log_exception = log_exception
    _remote_compat_error = compatibility_error
    _remote_request_error = request_error
    globals()["http_get"] = http_get
    globals()["http_post"] = http_post
    globals()["log_exception"] = log_exception
    globals()["RemoteTokenCompatibilityError"] = compatibility_error
    globals()["RemoteTokenRequestError"] = request_error


def resolve_grok2api_local_token_file():
    configured = str(config.get("grok2api_local_token_file", "") or "").strip()
    if configured:
        return configured
    return os.path.join(os.path.dirname(__file__), "token.json")

def _normalize_sso_token(raw_token):
    token = str(raw_token or "").strip()
    if token.startswith("sso="):
        token = token[4:]
    return token

def add_token_to_grok2api_local_pool(raw_token, email="", log_callback=None):
    token = _normalize_sso_token(raw_token)
    if not token:
        return False
    token_file = os.path.abspath(resolve_grok2api_local_token_file())
    pool_name = str(config.get("grok2api_pool_name", "ssoBasic") or "ssoBasic").strip() or "ssoBasic"
    parent = os.path.dirname(token_file)
    os.makedirs(parent, exist_ok=True)
    lock_path = token_file + ".lock"
    try:
        with open(lock_path, "a", encoding="utf-8"):
            pass
        os.chmod(lock_path, 0o600)
    except Exception:
        pass
    try:
        from filelock import FileLock
    except Exception as exc:
        raise RuntimeError(f"filelock dependency unavailable, refusing non-atomic token pool write: {exc}")
    with FileLock(lock_path, timeout=30):
        data = {}
        if os.path.exists(token_file):
            try:
                with open(token_file, "r", encoding="utf-8") as f:
                    data = json.load(f) or {}
            except Exception as exc:
                broken_path = token_file + f".broken-{int(time.time())}"
                try:
                    os.replace(token_file, broken_path)
                except Exception:
                    broken_path = token_file
                raise RuntimeError(f"Local token file JSON parse failed, stopped write to avoid overwrite: {broken_path}: {exc}")
        if not isinstance(data, dict):
            raise RuntimeError("Local token file root is not a JSON object, refusing to overwrite")
        pool = data.get(pool_name)
        if pool is None:
            pool = []
        elif not isinstance(pool, list):
            raise RuntimeError(f"Local token pool {pool_name} is not a list, refusing to overwrite")
        existing = set()
        for item in pool:
            if isinstance(item, str):
                existing.add(_normalize_sso_token(item))
            elif isinstance(item, dict):
                existing.add(_normalize_sso_token(item.get("token", "")))
        if token in existing:
            if log_callback:
                log_callback(f"[*] grok2api local pool already has token: {pool_name}")
            return True
        pool.append({"token": token, "tags": ["auto-register"], "note": email})
        data[pool_name] = pool
        if os.path.exists(token_file):
            backup_path = token_file + ".bak"
            try:
                with open(token_file, "rb") as src, open(backup_path, "wb") as dst:
                    dst.write(src.read())
                    dst.flush()
                    os.fsync(dst.fileno())
                try:
                    os.chmod(backup_path, 0o600)
                except Exception:
                    pass
            except Exception as exc:
                raise RuntimeError(f"Failed to create local token backup, refusing to continue write: {exc}")
        fd, temp_path = tempfile.mkstemp(prefix=".token-", suffix=".tmp", dir=parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            try:
                os.chmod(temp_path, 0o600)
            except Exception:
                pass
            os.replace(temp_path, token_file)
            temp_path = None
            try:
                os.chmod(token_file, 0o600)
            except Exception:
                pass
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except Exception:
                    pass
    if log_callback:
        log_callback(f"[+] Written to grok2api local pool: {pool_name} ({token_file})")
    return True

def get_grok2api_remote_api_bases(base):
    """生成 grok2api 管理 API 候选根路径。

    参数:
      - base str: 用户配置的 grok2api 远端地址

    返回:
      - list[str]: 依次尝试的管理 API 根路径
    """
    normalized = str(base or "").strip().rstrip("/")
    if not normalized:
        return []
    lower = normalized.lower()
    candidates = [normalized]
    if lower.endswith("/admin/api"):
        return candidates
    if lower.endswith("/admin"):
        candidates.append(f"{normalized}/api")
    else:
        candidates.append(f"{normalized}/admin/api")
    seen = set()
    unique = []
    for item in candidates:
        if item not in seen:
            unique.append(item)
            seen.add(item)
    return unique

def add_token_to_grok2api_remote_pool(raw_token, email="", log_callback=None):
    token = _normalize_sso_token(raw_token)
    if not token:
        return False
    base = str(config.get("grok2api_remote_base", "") or "").strip().rstrip("/")
    app_key = str(config.get("grok2api_remote_app_key", "") or "").strip()
    pool_name = str(config.get("grok2api_pool_name", "ssoBasic") or "ssoBasic").strip()
    if not base or not app_key:
        raise RemoteTokenRequestError("grok2api remote base/app_key not configured")
    headers = {"Content-Type": "application/json"}
    query = {"app_key": app_key}
    remote_pool = {"ssoBasic": "basic", "ssoSuper": "super"}[pool_name]
    api_bases = get_grok2api_remote_api_bases(base)
    incompatible = []
    add_payload = {"tokens": [token], "pool": remote_pool, "tags": ["auto-register"]}
    for api_base in api_bases:
        endpoint = f"{api_base}/tokens/add"
        try:
            response = http_post(endpoint, headers=headers, params=query, json=add_payload, timeout=30)
        except Exception as exc:
            raise RemoteTokenRequestError(f"Remote /tokens/add network request failed: {endpoint}: {exc}") from exc
        status = int(getattr(response, "status_code", 0) or 0)
        if 200 <= status < 300:
            if log_callback:
                log_callback(f"[+] Written to grok2api remote pool: {pool_name} ({endpoint})")
            return True
        if status in (404, 405):
            incompatible.append(f"{endpoint}: HTTP {status}")
            continue
        body = str(getattr(response, "text", "") or "")[:300]
        raise RemoteTokenRequestError(f"Remote /tokens/add request failed, full fallback not allowed: {endpoint}: HTTP {status}: {body}")
    if not bool(config.get("grok2api_allow_legacy_full_save", False)):
        raise RemoteTokenCompatibilityError(
            "/tokens/add not supported, legacy full save disabled by default to avoid concurrent overwrite: " + "; ".join(incompatible)
        )
    current = None
    fallback_base = None
    etag = None
    load_errors = []
    for api_base in api_bases or [base]:
        endpoint = f"{api_base}/tokens"
        try:
            response = http_get(endpoint, headers=headers, params=query, timeout=20)
        except Exception as exc:
            raise RemoteTokenRequestError(f"Legacy remote pool read network failure: {endpoint}: {exc}") from exc
        status = int(getattr(response, "status_code", 0) or 0)
        if status != 200:
            load_errors.append(f"{endpoint}: HTTP {status}")
            continue
        payload = response.json()
        candidate = payload.get("tokens") if isinstance(payload, dict) and "tokens" in payload else payload
        if not isinstance(candidate, dict):
            load_errors.append(f"{endpoint}: unexpected payload")
            continue
        current = candidate
        fallback_base = api_base
        response_headers = getattr(response, "headers", {}) or {}
        etag = response_headers.get("ETag") or response_headers.get("etag")
        break
    if current is None or fallback_base is None:
        raise RemoteTokenRequestError("Cannot safely read legacy remote token pool: " + "; ".join(load_errors))
    pool = current.get(pool_name)
    if pool is None:
        pool = []
    elif not isinstance(pool, list):
        raise RemoteTokenRequestError(f"Remote token pool {pool_name} is not a list, refusing full overwrite")
    existing = {
        _normalize_sso_token(item if isinstance(item, str) else item.get("token", ""))
        for item in pool if isinstance(item, (str, dict))
    }
    if token not in existing:
        pool.append({"token": token, "tags": ["auto-register"], "note": email})
    current[pool_name] = pool
    if not etag:
        raise RemoteTokenCompatibilityError(
            "Legacy remote API did not provide ETag, cannot guarantee concurrency safety, full save rejected"
        )
    save_headers = dict(headers)
    save_headers["If-Match"] = etag
    endpoint = f"{fallback_base}/tokens"
    try:
        response = http_post(endpoint, headers=save_headers, params=query, json=current, timeout=30)
    except Exception as exc:
        raise RemoteTokenRequestError(f"Legacy remote pool save network failure: {endpoint}: {exc}") from exc
    status = int(getattr(response, "status_code", 0) or 0)
    if not 200 <= status < 300:
        raise RemoteTokenRequestError(f"Legacy remote pool save failed: {endpoint}: HTTP {status}")
    if log_callback:
        log_callback(f"[+] Written to grok2api remote pool (legacy compat): {pool_name} ({endpoint})")
    return True

def add_token_to_grok2api_pools(raw_token, email="", log_callback=None):
    result = {
        "local": {"enabled": bool(config.get("grok2api_auto_add_local", False)), "ok": None, "error": None},
        "remote": {"enabled": bool(config.get("grok2api_auto_add_remote", False)), "ok": None, "error": None},
    }
    if result["local"]["enabled"]:
        try:
            result["local"]["ok"] = bool(add_token_to_grok2api_local_pool(raw_token, email=email, log_callback=log_callback))
        except Exception as exc:
            result["local"]["ok"] = False
            result["local"]["error"] = log_exception("Failed to write to grok2api local pool", exc, log_callback)
    if result["remote"]["enabled"]:
        try:
            result["remote"]["ok"] = bool(add_token_to_grok2api_remote_pool(raw_token, email=email, log_callback=log_callback))
        except Exception as exc:
            result["remote"]["ok"] = False
            result["remote"]["error"] = log_exception("Failed to write to grok2api remote pool", exc, log_callback)
    return result

