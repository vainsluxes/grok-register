"""Grok Manager web dashboard — FastAPI app."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

REG_DIR = Path(__file__).resolve().parents[1]
if str(REG_DIR) not in sys.path:
    sys.path.insert(0, str(REG_DIR))

from dashboard import accounts as acc
from dashboard import db as dbmod
from dashboard.jobs import jobs

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Grok Manager", version="1.0.0")


# ---------- models ----------

class DeleteIdsBody(BaseModel):
    ids: List[str] = Field(default_factory=list)


class DeleteFilterBody(BaseModel):
    provider: Optional[str] = None  # grok-cli | grok-web
    inactive_only: bool = False
    all_provider: bool = False
    buckets: Optional[List[str]] = None


class RegisterBody(BaseModel):
    count: int = 1
    # Optional one-shot override for this register job (normal|minimize|xvfb)
    browser_hide_mode: Optional[str] = None


class ConfigUpdateBody(BaseModel):
    browser_hide_mode: Optional[str] = None
    cpa_export_enabled: Optional[bool] = None
    cpa_push_9router: Optional[bool] = None
    cpa_save_auth_file: Optional[bool] = None
    register_count: Optional[int] = None
    enable_nsfw: Optional[bool] = None
    email_provider: Optional[str] = None
    cpa_headless: Optional[bool] = None


class MintBody(BaseModel):
    limit: int = 5
    sleep: float = 15.0
    save_cpa: bool = False


class ImportCpaBody(BaseModel):
    emails: Optional[List[str]] = None  # None = all cpa files
    provider: str = "grok-cli"  # grok-cli only for cpa oauth


class ImportAccountsWebBody(BaseModel):
    path: Optional[str] = None  # default latest
    limit: Optional[int] = None


class ProbeBody(BaseModel):
    provider: str = "grok-cli"
    # auto = unknown, else inactive, else all
    # unknown | inactive | all | ids | auto
    mode: str = "auto"
    live: bool = True  # True: refresh+models probe (detects quota). False: 9router test only
    sleep: float = 1.5
    limit: Optional[int] = None
    ids: Optional[List[str]] = None


# ---------- pages ----------

@app.get("/", response_class=HTMLResponse)
def index():
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        return HTMLResponse("<h1>Grok Manager</h1><p>static/index.html missing</p>", status_code=500)
    return FileResponse(index_path)


# ---------- status ----------

@app.get("/api/health")
def health():
    return {
        "ok": True,
        "reg_dir": str(REG_DIR),
        "db_path": dbmod.DB_PATH,
        "db_exists": os.path.exists(dbmod.DB_PATH),
    }


@app.get("/api/status")
def status():
    try:
        summary = dbmod.summary()
    except Exception as exc:
        summary = {"error": str(exc), "providers": {}, "db_exists": os.path.exists(dbmod.DB_PATH)}
    local = acc.local_overview()
    return {"nine_router": summary, "local": local, "jobs": jobs.list_jobs(10)}


@app.get("/api/connections")
def connections(provider: Optional[str] = None, bucket: Optional[str] = None, active: Optional[bool] = None):
    try:
        rows = dbmod.list_connections(provider)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))
    if bucket:
        rows = [r for r in rows if r["bucket"] == bucket]
    if active is not None:
        rows = [r for r in rows if r["active"] is active]
    return {"count": len(rows), "items": rows}


# ---------- delete ----------

@app.post("/api/connections/delete")
def delete_ids(body: DeleteIdsBody):
    if not body.ids:
        raise HTTPException(400, "ids required")
    n = dbmod.delete_by_ids(body.ids)
    return {"deleted": n}


@app.post("/api/connections/delete-filter")
def delete_filter(body: DeleteFilterBody):
    if not body.provider and not body.all_provider and not body.inactive_only and not body.buckets:
        raise HTTPException(400, "provide provider/inactive_only/buckets/all_provider")
    n, emails = dbmod.delete_by_filter(
        provider=body.provider,
        buckets=body.buckets,
        inactive_only=body.inactive_only,
        all_provider=body.all_provider,
    )
    return {"deleted": n, "emails": emails[:50], "emails_total": len(emails)}


# ---------- local accounts / cpa ----------

@app.get("/api/accounts/files")
def account_files():
    return {"items": acc.list_account_files()}


@app.get("/api/accounts")
def accounts(path: Optional[str] = None):
    p = path or acc.latest_accounts_path()
    if not p:
        return {"path": None, "count": 0, "items": []}
    items = acc.parse_accounts_file(p)
    # never return full password/sso in list by default — mask
    safe = []
    for a in items:
        safe.append(
            {
                "email": a["email"],
                "has_password": bool(a.get("password")),
                "has_sso": bool(a.get("sso")),
                "sso_preview": (a.get("sso") or "")[:12] + "..." if a.get("sso") else "",
            }
        )
    return {"path": p, "count": len(safe), "items": safe}


@app.get("/api/cpa")
def cpa_list():
    return {"count": len(acc.list_cpa_auths()), "items": acc.list_cpa_auths()}


@app.get("/api/cpa/failed")
def cpa_failed(limit: int = 50):
    return {"items": acc.failed_cpa_log(limit)}


# ---------- import ----------

@app.post("/api/import/cpa")
def import_cpa(body: ImportCpaBody):
    """Import CPA JSON tokens into 9router grok-cli (sync, no browser)."""
    items = acc.list_cpa_auths()
    if body.emails:
        want = {e.lower() for e in body.emails}
        items = [i for i in items if (i.get("email") or "").lower() in want]

    inserted = updated = failed = 0
    errors = []
    for item in items:
        email = item.get("email") or ""
        data = acc.read_cpa_auth(email)
        if not data or not data.get("access_token"):
            failed += 1
            errors.append({"email": email, "error": "no tokens"})
            continue
        try:
            action = dbmod.upsert_grok_cli(email, data)
            if action == "inserted":
                inserted += 1
            else:
                updated += 1
        except Exception as exc:
            failed += 1
            errors.append({"email": email, "error": str(exc)})
    return {
        "ok": True,
        "scanned": len(items),
        "inserted": inserted,
        "updated": updated,
        "failed": failed,
        "errors": errors[:20],
    }


@app.post("/api/import/accounts-web")
def import_accounts_web(body: ImportAccountsWebBody):
    """Import SSO from accounts_*.txt into grok-web (sync)."""
    path = body.path or acc.latest_accounts_path()
    if not path:
        raise HTTPException(404, "no accounts file")
    accounts = acc.parse_accounts_file(path)
    if body.limit is not None:
        accounts = accounts[: max(0, int(body.limit))]

    inserted = updated = skipped = failed = 0
    errors = []
    for a in accounts:
        sso = a.get("sso") or ""
        if not sso:
            skipped += 1
            continue
        try:
            action = dbmod.upsert_grok_web(a["email"], sso)
            if action == "inserted":
                inserted += 1
            else:
                updated += 1
        except Exception as exc:
            failed += 1
            errors.append({"email": a["email"], "error": str(exc)})
    return {
        "ok": True,
        "path": path,
        "scanned": len(accounts),
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "failed": failed,
        "errors": errors[:20],
    }


# ---------- jobs: register / mint ----------

@app.get("/api/jobs")
def list_jobs():
    return {"items": jobs.list_jobs(50)}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, logs: bool = True, tail: int = 300):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return job.to_dict(with_logs=logs, log_tail=tail)


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    ok = jobs.cancel(job_id)
    if not ok:
        raise HTTPException(400, "cannot cancel (not running or missing)")
    return {"ok": True}


@app.post("/api/jobs/register")
def job_register(body: RegisterBody):
    hide = None
    if body.browser_hide_mode:
        hide = str(body.browser_hide_mode).strip().lower()
        if hide not in ("normal", "minimize", "xvfb", "min", "minimized", "xvfb-run", "virtual"):
            raise HTTPException(400, "browser_hide_mode must be normal|minimize|xvfb")
    job = jobs.start_register(count=body.count, browser_hide_mode=hide)
    return {"ok": True, "job": job.to_dict()}


@app.post("/api/jobs/mint")
def job_mint(body: MintBody):
    job = jobs.start_mint(limit=body.limit, sleep=body.sleep, save_cpa=body.save_cpa)
    return {"ok": True, "job": job.to_dict()}


@app.post("/api/jobs/import-cpa-script")
def job_import_cpa_script():
    """Run legacy import_to_vps_db.py as background job."""
    job = jobs.start_import_cpa()
    return {"ok": True, "job": job.to_dict()}


@app.post("/api/jobs/probe")
def job_probe(body: ProbeBody):
    """Probe connections so unknown → active / quota_exhausted / error.

    live=True (default): refresh token + GET /v1/models on xAI (can detect free-usage-exhausted).
    live=False: only 9Router /api/providers/{id}/test (often just checks token exists for grok-cli).
    """
    mode = (body.mode or "auto").strip().lower()
    if mode not in ("auto", "unknown", "inactive", "all", "ids"):
        raise HTTPException(400, "mode must be auto|unknown|inactive|all|ids")
    if mode == "ids" and not body.ids:
        raise HTTPException(400, "ids required when mode=ids")
    provider = (body.provider or "grok-cli").strip()
    if provider not in ("grok-cli", "grok-web"):
        raise HTTPException(400, "provider must be grok-cli or grok-web")
    job = jobs.start_probe(
        provider=provider,
        mode=mode,
        live=bool(body.live),
        sleep_sec=float(body.sleep),
        limit=body.limit,
        ids=body.ids,
    )
    return {"ok": True, "job": job.to_dict()}


# ---------- config (read-only summary) ----------

@app.get("/api/config")
def get_config():
    cfg_path = REG_DIR / "config.json"
    if not cfg_path.exists():
        return {"exists": False, "config": {}}
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(500, f"config parse error: {exc}")
    # redact secrets lightly
    safe = dict(cfg)
    for k in list(safe.keys()):
        if any(s in k.lower() for s in ("key", "token", "password", "secret", "jwt")) and safe.get(k):
            val = str(safe[k])
            safe[k] = val[:4] + "…" if len(val) > 4 else "***"
    return {
        "exists": True,
        "path": str(cfg_path),
        "config": safe,
        "browser_hide_modes": ["normal", "minimize", "xvfb"],
    }


@app.patch("/api/config")
def patch_config(body: ConfigUpdateBody):
    """Update a few safe config keys (including browser_hide_mode)."""
    cfg_path = REG_DIR / "config.json"
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
    except Exception as exc:
        raise HTTPException(500, f"config parse error: {exc}")

    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(400, "no fields to update")

    if "browser_hide_mode" in updates:
        mode = str(updates["browser_hide_mode"]).strip().lower()
        if mode in ("min", "minimized", "minimise"):
            mode = "minimize"
        if mode in ("virtual", "virtual-display", "xvfb-run"):
            mode = "xvfb"
        if mode not in ("normal", "minimize", "xvfb"):
            raise HTTPException(400, "browser_hide_mode must be normal|minimize|xvfb")
        updates["browser_hide_mode"] = mode

    if "email_provider" in updates:
        ep = str(updates["email_provider"]).strip().lower()
        if ep not in ("duckmail", "yyds", "cloudflare", "cloudmail"):
            raise HTTPException(400, "invalid email_provider")
        updates["email_provider"] = ep

    if "register_count" in updates:
        try:
            rc = int(updates["register_count"])
        except Exception:
            raise HTTPException(400, "register_count must be int")
        if not 1 <= rc <= 2500:
            raise HTTPException(400, "register_count out of range")
        updates["register_count"] = rc

    cfg.update(updates)
    try:
        # Validate via app_config if available
        from app_config import validate_config_structure

        cfg = validate_config_structure(cfg)
    except Exception as exc:
        raise HTTPException(400, f"config invalid: {exc}")

    tmp = cfg_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=4, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, cfg_path)
    try:
        os.chmod(cfg_path, 0o600)
    except Exception:
        pass

    safe = dict(cfg)
    for k in list(safe.keys()):
        if any(s in k.lower() for s in ("key", "token", "password", "secret", "jwt")) and safe.get(k):
            val = str(safe[k])
            safe[k] = val[:4] + "…" if len(val) > 4 else "***"
    return {"ok": True, "updated": list(updates.keys()), "config": safe}


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
